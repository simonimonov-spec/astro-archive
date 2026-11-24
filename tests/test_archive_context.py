# Beta 1.3 UI
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from astropy.io import fits
import numpy as np

from Backend.Archive.catalog import Catalog
from Backend.Archive.editing import ArchiveEditor
from Backend.Foundation.branding import companion
from Backend.Foundation.runtime import Events, Settings, Store, atomic_json
from Backend.Fits.images import metadata


class ArchiveContextTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'frames'
        self.root.mkdir()
        store=Store(Path(temp.name)/'data')
        settings=Settings(store)
        settings.value['archives'][0]['path']=str(self.root)
        self.catalog=Catalog(store,settings,Events())
        self.editor=ArchiveEditor(self.catalog)
        self.cancel=threading.Event()
        self.progress=lambda *args: None

    def image(self,relative,telescope='T1',obj='M31',night='2026-01-02',archive='frames'):
        path=self.catalog.root(archive)/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        data=np.arange(12,dtype=np.int16).reshape(3,4)
        header=fits.Header({'TELESCOP':telescope,'OBJECT':obj,'PSNIGHT':night,
                            'DATE-OBS':'2026-01-03T00:10:00','FILTER':'R','EXPTIME':125.0})
        fits.writeto(path,data,header,checksum=True)
        atomic_json(companion(path),{'tag':{'kind':'photometry','type':'variable'},'note':'custom note'})
        return self.catalog.put(archive,path,metadata(path)),path

    def commit(self,preview):
        return self.editor.commit(preview['token'],self.cancel,self.progress,confirmed=True)

    def test_move_to_existing_leaf_uses_neighbor_fields_and_preserves_pixels_and_observation(self):
        ident,source=self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        original=source.read_bytes()
        target_relative='T2/M42/Наблюдателна нощ 2026-01-04'
        _,neighbor=self.image(target_relative+'/b.fits','T2','M42','2026-01-04')
        preview=self.editor.move_preview('frames',str(source.relative_to(self.root)),'frames',target_relative)
        self.assertEqual(preview['examples'][0]['after'],target_relative+'/a.fits')
        self.assertEqual(len(preview['examples'][0]['fields']),3)
        self.assertTrue(source.exists())
        result=self.commit(preview)
        target=self.root/target_relative/'a.fits'
        with fits.open(target,checksum=True) as hdus:
            header=hdus[0].header
            self.assertEqual((header['TELESCOP'],header['OBJECT'],header['PSNIGHT']),('T2','M42','2026-01-04'))
            self.assertEqual((header['DATE-OBS'],header['EXPTIME'],header['FILTER']),('2026-01-03T00:10:00',125.0,'R'))
            np.testing.assert_array_equal(hdus[0].data,np.arange(12,dtype=np.int16).reshape(3,4))
        preserved=json.loads(companion(target).read_text(encoding='utf-8'))
        self.assertEqual(preserved['note'],'custom note')
        self.assertEqual(preserved['tag']['type'],'variable')
        self.assertEqual(self.catalog.record(ident)['relative'],target_relative+'/a.fits')
        self.assertEqual((Path(result['backup'])/source.relative_to(self.root)).read_bytes(),original)
        self.assertTrue(neighbor.exists())
        self.assertFalse(source.exists())

    def test_move_recreates_lower_levels_and_can_use_another_archive_of_the_same_type(self):
        self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        other=self.root.parent/'frames-other'
        (other/'T3').mkdir(parents=True)
        self.catalog.settings.value['archives'].append(dict(id='other',name='Other',path=str(other),type='frames'))
        preview=self.editor.move_preview('frames','T1/M31','other','T3')
        self.assertEqual(preview['examples'][0]['after'],'T3/M31/Наблюдателна нощ 2026-01-02/a.fits')
        self.commit(preview)
        row,=self.catalog.query(dict(archive='other'))
        self.assertEqual(row['meta']['telescope'],'T3')

    def test_non_empty_destination_never_overwrites_an_existing_file(self):
        _,source=self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        target='T2/M42/Наблюдателна нощ 2026-01-04'
        _,existing=self.image(target+'/a.fits','T2','M42','2026-01-04')
        saved=existing.read_bytes()
        preview=self.editor.move_preview('frames',str(source.relative_to(self.root)),'frames',target)
        self.assertNotEqual(preview['examples'][0]['after'],target+'/a.fits')
        self.commit(preview)
        self.assertEqual(existing.read_bytes(),saved)

    def test_batch_naming_stays_inside_the_selected_folder(self):
        ident,path=self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        _,outside=self.image('T2/M42/Наблюдателна нощ 2026-01-02/b.fits','T2','M42')
        preview=self.editor.rename_preview('frames','T1',['Цел {object}','{night}'],'{basename}_R{extension}')
        self.assertEqual(preview['examples'][0]['after'],'T1/Цел M31/2026-01-02/a_R.fits')
        self.assertEqual(preview['examples'][0]['fields'],[])
        raw=path.read_bytes()
        self.commit(preview)
        self.assertEqual(Path(self.catalog.path(ident)).read_bytes(),raw)
        self.assertTrue(outside.exists())

    def test_changed_companion_and_failed_write_leave_source_intact(self):
        ident,source=self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        target=self.root/'T2'
        target.mkdir()
        preview=self.editor.move_preview('frames',str(source.relative_to(self.root)),'frames','T2')
        original=source.read_bytes()
        with patch('Backend.Archive.editing.os.link',side_effect=FileExistsError('conflict')):
            with self.assertRaises(FileExistsError):self.commit(preview)
        self.assertEqual(source.read_bytes(),original)
        self.assertEqual(self.catalog.path(ident),source)
        preview=self.editor.move_preview('frames',str(source.relative_to(self.root)),'frames','T2')
        atomic_json(companion(source),dict(object='Changed elsewhere'))
        with self.assertRaisesRegex(ValueError,'Метаданните'):self.commit(preview)
        self.assertEqual(source.read_bytes(),original)

    def test_delete_folder_is_recoverable_and_stays_in_the_selected_scope(self):
        _,selected=self.image('T1/M31/Наблюдателна нощ 2026-01-02/a.fits')
        _,other=self.image('T1/M42/Наблюдателна нощ 2026-01-02/b.fits',obj='M42')
        raw=selected.read_bytes()
        result=self.editor.delete_selection('frames','T1/M31',self.cancel,self.progress,True)
        self.assertFalse(selected.exists())
        self.assertTrue(other.exists())
        entry,=result['trashed']
        self.catalog.restore(entry)
        self.assertEqual(selected.read_bytes(),raw)

    def test_empty_folder_can_be_moved_without_fits_changes(self):
        source=self.root/'empty'
        target=self.root/'other'
        source.mkdir()
        target.mkdir()
        preview=self.editor.move_preview('frames','empty','frames','other')
        self.assertFalse(preview['backup'])
        self.assertTrue(self.commit(preview)['movedFolder'])
        self.assertTrue((target/'empty').is_dir())
        self.assertFalse(source.exists())


if __name__=='__main__':
    unittest.main()

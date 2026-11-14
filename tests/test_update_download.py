"""Verified downloads preserve old files and discard failed partial transfers."""
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from Backend.Foundation.update_download import package_for,download


class Response(io.BytesIO):
    url='https://release-assets.githubusercontent.com/package.zip'


class UpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'Updates'
        self.data=b'PK'+b'Windows release data'*20000
        self.name='AstroArchive_Beta1.2_Windows_x64.zip'
        self.asset=dict(name=self.name,size=len(self.data),digest='sha256:'+hashlib.sha256(self.data).hexdigest(),browser_download_url='https://github.com/simonimonov-spec/astro-archive/releases/download/beta1.2/'+self.name)
        self.release=dict(tag_name='beta1.2',assets=[self.asset])
        self.package=package_for(self.release);self.cancel=threading.Event();self.progress=[]

    def run_download(self,response=None):
        with patch('Backend.Foundation.update_download.urllib.request.urlopen',return_value=response or Response(self.data)):
            return Path(download(self.package,self.folder,self.cancel,lambda done,total:self.progress.append((done,total))))

    def test_selects_only_unambiguous_official_windows_zip(self):
        self.assertEqual(self.package['name'],self.name)
        for fields in ({'name':'../escape.zip'},{'digest':None},{'size':True},{'browser_download_url':'https://example.com/package.zip'}):
            with self.subTest(fields=fields):self.assertIsNone(package_for(dict(self.release,assets=[dict(self.asset,**fields)])))
        self.assertIsNone(package_for(dict(self.release,assets=[self.asset,self.asset])))

    def test_streams_then_verifies_and_reuses_complete_file(self):
        destination=self.run_download()
        self.assertEqual(destination.read_bytes(),self.data)
        self.assertEqual(self.progress[-1],(len(self.data),len(self.data)))
        with patch('Backend.Foundation.update_download.urllib.request.urlopen',side_effect=AssertionError('No second download expected')):
            self.assertEqual(Path(download(self.package,self.folder,self.cancel,lambda *_:None)),destination)

    def test_hash_or_size_failure_leaves_no_partial_or_empty_directory(self):
        for data in (b'short',self.data+b'overflow',b'X'*len(self.data)):
            with self.subTest(length=len(data)),self.assertRaises(ValueError):self.run_download(Response(data))
            self.assertFalse(self.folder.exists())

    def test_cancel_after_first_chunk_cleans_up_and_can_retry(self):
        def progress(*_):self.cancel.set()
        with patch('Backend.Foundation.update_download.urllib.request.urlopen',return_value=Response(self.data)),self.assertRaises(InterruptedError):download(self.package,self.folder,self.cancel,progress)
        self.assertFalse(self.folder.exists())
        self.cancel.clear();self.assertTrue(self.run_download().exists())

    def test_network_failure_after_some_bytes_cleans_partial(self):
        class Broken(Response):
            def read(self,*args):
                if self.tell():raise OSError('connection interrupted')
                return super().read(*args)
        with self.assertRaises(OSError):self.run_download(Broken(self.data))
        self.assertFalse(self.folder.exists())

    def test_never_overwrites_existing_different_file(self):
        self.folder.mkdir();original=self.folder/self.name;original.write_bytes(b'keep existing file')
        destination=self.run_download()
        self.assertNotEqual(destination,original)
        self.assertEqual(original.read_bytes(),b'keep existing file')
        self.assertEqual(destination.read_bytes(),self.data)

    def test_rejects_unexpected_final_host(self):
        response=Response(self.data);response.url='https://example.com/package.zip'
        with self.assertRaises(ValueError):self.run_download(response)
        self.assertFalse(self.folder.exists())


if __name__=='__main__':unittest.main()

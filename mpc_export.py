"""IAU ADES conversion, with local bounds and numerical round-trip checks.

No hand-written coordinate/date/designation encoder and no report submission.
The separately installed iau-ades package owns the format conversion.
"""
from datetime import datetime,timezone
from importlib import metadata
from pathlib import Path
import hashlib,math,os,re,subprocess,sys,tempfile,xml.etree.ElementTree as ET
from astropy.time import Time
from .downloads import check_cancel

VERSION='0.1.3'
HEADERS=('COD ','CON ','OBS ','MEA ','TEL ','NET ','ACK ','COM ','AC2 ')

def fingerprint(text):return hashlib.sha256(text.replace('\r\n','\n').encode('utf-8')).hexdigest()

def available():
    try:return metadata.version('iau-ades')==VERSION
    except metadata.PackageNotFoundError:return False

def telescope_description(values):
    description=values.get('design','').strip();detector=values.get('detector','').strip()
    if not description or not detector or any(c in description+detector for c in '\r\n\0'):raise ValueError('Липсва еднозначно описание на телескопа/детектора в ADES.')
    # Astrometrica can put a complete TEL string in design and use 0.0 as
    # the separate unknown aperture. Preserve the complete real description.
    has_aperture=bool(re.search(r'\d+(?:[.,]\d+)?\s*[- ]?\s*(?:m|cm|mm)\b',description,re.I))
    if not has_aperture:
        try:aperture=float(values.get('aperture',''))
        except ValueError:aperture=0.
        if not math.isfinite(aperture) or aperture<=0:raise ValueError('Липсва реална апертура на телескопа в ADES.')
        description=f'{aperture:g}-m '+description
    if not re.search(r'\b'+re.escape(detector)+r'\b',description,re.I):description+=' + '+detector
    return description

def run(module,input_,output,cancel):
    check_cancel(cancel)
    command=[sys.executable,'-m','ades.'+module,str(input_),str(output)]
    env=dict(os.environ,PYTHONUTF8='1')
    try:
        result=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=20,
                              env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:raise ValueError('IAU конверторът не завърши в зададеното време.') from None
    check_cancel(cancel)
    if result.returncode or not output.is_file() or output.stat().st_size>512000:
        # Upstream diagnostics can contain input data. Do not leak them to a log.
        raise ValueError('IAU конверторът отказа отчета. Провери ADES полетата и означенията на обектите; оригиналът е запазен.')

def observations(root):
    if any(n.tag.rsplit('}',1)[-1] in ('radar','offset','occultation') for n in root.iter()):
        raise ValueError('IASC износът поддържа само обичайни оптични измервания.')
    return [{n.tag.rsplit('}',1)[-1]:n.text.strip() for n in row if n.text and n.text.strip()} for row in root.iter('optical')]

def check_roundtrip(before,after):
    if not before or len(before)!=len(after):raise ValueError('Броят измервания не се запазва при конвертирането.')
    max_ra=max_dec=max_time=max_mag=0.
    for a,b in zip(before,after):
        for key in ('permID','provID','trkSub','stn','mode'):
            first,second=a.get(key,''),b.get(key,'')
            if key=='permID' and first.isdigit() and second.isdigit():first,second=str(int(first)),str(int(second))
            if first and first!=second:raise ValueError('Идентификаторът, обсерваторията или типът измерване се променят при конвертиране.')
        ra=abs((float(a['ra'])-float(b['ra'])+180)%360-180)*3600
        dec=abs(float(a['dec'])-float(b['dec']))*3600
        elapsed=abs(float((Time(a['obsTime'].rstrip('Z'),format='isot',scale='utc')-Time(b['obsTime'].rstrip('Z'),format='isot',scale='utc')).sec))
        if not all(math.isfinite(v) for v in (ra,dec,elapsed)) or ra>.008 or dec>.006 or elapsed>.044:
            raise ValueError('Координатите или времето се променят повече от закръгляването на MPC формата.')
        if a.get('mag') and b.get('mag'):
            mag=abs(float(a['mag'])-float(b['mag']));max_mag=max(max_mag,mag)
            if mag>.051:raise ValueError('Звездната величина се променя повече от закръгляването на MPC формата.')
        if a.get('band') and a.get('band')!=b.get('band'):raise ValueError('Фотометричният канал се променя при конвертирането.')
        max_ra,max_dec,max_time=max(max_ra,ra),max(max_dec,dec),max(max_time,elapsed)
    return dict(rows=len(before),maxRaArcsec=max_ra,maxDecArcsec=max_dec,maxTimeSeconds=max_time,maxMag=max_mag)

def convert(text,folder,cancel):
    if len(text)>64000 or not text.strip():raise ValueError('Избери ADES отчет до 64 000 знака.')
    if not available():raise ValueError('Липсва iau-ades 0.1.3. Инсталирай requirements-export.txt от папката на плъгина и рестартирай Astro Archive.')
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='iau-convert-',dir=folder) as temporary:
        root=Path(temporary).resolve()
        if not root.is_relative_to(folder):raise ValueError('Невалидна временна папка за конвертиране.')
        xml=root/'input.xml';out=root/'output.txt';back=root/'roundtrip.xml'
        if text.lstrip().startswith(('<?xml','<ades')):
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('XML DTD и външни обекти не са разрешени.')
            xml.write_text(text,encoding='utf-8')
        else:
            psv=root/'input.psv';psv.write_text(text,encoding='utf-8');run('psvtoxml',psv,xml,cancel)
        tree=ET.parse(xml).getroot();source=observations(tree)
        if not source or len(source)>1000:raise ValueError('Очакват се от 1 до 1000 оптични измервания.')
        stations={r.get('stn','') for r in source};catalogs={r.get('astCat','') for r in source}
        if len(stations)!=1 or '' in stations or len(catalogs)!=1 or '' in catalogs:raise ValueError('Износът за един IASC комплект изисква една обсерватория и един посочен звезден каталог.')
        run('xmltompc80col',xml,out,cancel);run('mpc80coltoxml',out,back,cancel)
        verification=check_roundtrip(source,observations(ET.parse(back).getroot()))
        headers=[];rows=[]
        for line in out.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            if line.startswith(HEADERS):headers.append(line)
            else:
                if len(line)!=80 or not line.isascii() or line[77:80] not in stations:raise ValueError('IAU резултатът не отговаря на изискваните 80 колони или код на обсерваторията.')
                rows.append(line)
        if len(rows)!=len(source):raise ValueError('Липсват измервания в MPC износа.')
        codes={line[4:].strip() for line in headers if line.startswith('COD ')}
        if codes!=stations:raise ValueError('Липсва или е различен COD кодът в ADES заглавката.')
        for prefix in ('OBS ','MEA '):
            if not any(h.startswith(prefix) and h[4:].strip() for h in headers):raise ValueError('В ADES липсват имената за '+prefix.strip()+'. Попълни реалните данни в Astrometrica.')
        if not any(h.startswith('TEL ') for h in headers):
            telescope=tree.find('.//obsContext/telescope')
            if telescope is None:raise ValueError('Липсва описание на телескопа в ADES.')
            values={n.tag:(n.text or '').strip() for n in telescope}
            headers.append('TEL '+telescope_description(values))
        if not any(h.startswith('NET ') for h in headers):headers.append('NET '+next(iter(catalogs)))
        created=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
        if not any(h.startswith('ACK ') for h in headers):headers.append('ACK Astro Archive IASC report '+created)
        result='\n'.join(headers+rows+['----- end -----',''])
        return dict(text=result,sourceHash=fingerprint(text),created=created,converter='iau-ades '+VERSION,verification=verification,exportVersion=1)

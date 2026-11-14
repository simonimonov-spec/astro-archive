"""Independent Remote Operation plugin. Core access is through the remote SDK only."""
from __future__ import annotations
import ipaddress,socket,threading
from pathlib import Path
from .server import RemoteServer
from .tray import Tray

def setup(context):
    controller=Controller(context);controller.register();controller.publish()
    if controller.options.get('autostart'):
        try:controller.start({})
        except Exception as error:controller.problem=str(error);controller.publish()
    return controller.close

def discover():
    addresses=[]
    try:addresses=socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:pass
    lan=next((a for a in addresses if a.startswith('192.168.')),next((a for a in addresses if a.startswith('10.')),''))
    vpn=next((a for a in addresses if ipaddress.ip_address(a) in ipaddress.ip_network('100.64.0.0/10')),'')
    return lan,vpn

class Controller:
    def __init__(self,ctx):
        self.ctx=ctx;self.folder=Path(ctx.storage());self.lock=threading.RLock();self.server=None;self.problem='';self.closed=False;self.tray=None
        lan,vpn=discover();self.options=dict(mode='lan',port=48732,lan=True,lanAddress=lan,lanNetwork=str(ipaddress.ip_network(lan+'/24',strict=False)) if lan else '192.168.0.0/24',vpnAddress=vpn,vpnPeers='',dns='',certificateFile='',privateKeyFile='',uploadMb=512,autostart=False)
        self.options.update({k:v for k,v in ctx.settings().items() if k in self.options})
    def register(self):
        for name,handler in [('start',self.start),('stop',self.stop),('configure',self.configure),('code',self.code),('revoke',self.revoke),('refresh',self.refresh),('copy',self.copy),('status',self.status)]:self.ctx.command(name,handler)
    def configure(self,p):
        if self.server:raise ValueError('Спри връзката преди промяна на адресите.')
        values=p.get('values',{});options=dict(self.options,**{k:v for k,v in values.items() if k in self.options})
        options=self.validate(options)
        self.options=options;self.ctx.save_settings(options);self.publish();return dict(saved=True)
    @staticmethod
    def validate(options,starting=False):
        options=dict(options)
        if options['mode'] not in ('lan','vpn'):raise ValueError('Избери домашна мрежа или VPN / Tailscale.')
        if type(options['port']) not in (int,float) or not 1024<=options['port']<=65535 or options['port']!=int(options['port']):raise ValueError('Портът трябва да е между 1024 и 65535.')
        options['port']=int(options['port'])
        if type(options['lan']) is not bool or type(options['autostart']) is not bool:raise ValueError('Невалидна отметка.')
        if type(options['uploadMb']) not in (int,float) or not 1<=options['uploadMb']<=1024:raise ValueError('Размерът е между 1 и 1024 MB.')
        options['uploadMb']=int(options['uploadMb'])
        if options['mode']=='lan' and options['lan']:
            ip=ipaddress.ip_address(options['lanAddress']);network=ipaddress.ip_network(options['lanNetwork'])
            if ip.version!=4 or not ip.is_private or ip.is_loopback or ip not in network or network.prefixlen<16:raise ValueError('Посочи конкретния LAN адрес и локална подмрежа (/16 или по-тясна).')
        if options['mode']=='vpn':
            network=ipaddress.ip_network('100.64.0.0/10')
            if (starting or options['vpnAddress']) and (not options['vpnAddress'] or ipaddress.ip_address(options['vpnAddress']) not in network):raise ValueError('Въведи IPv4 адреса на компютъра от Tailscale (100.x.x.x).')
            peers=[p.strip() for p in options['vpnPeers'].split(',') if p.strip()]
            if (starting and not peers) or len(peers)>16 or any(ipaddress.ip_address(p) not in network for p in peers):raise ValueError('Посочи до 16 точни Tailscale IPv4 адреса на разрешените устройства, разделени със запетая.')
            options['vpnPeers']=', '.join(dict.fromkeys(str(ipaddress.ip_address(p)) for p in peers))
        options['dns']=str(options['dns']).strip().lower().rstrip('.')
        for key in ('certificateFile','privateKeyFile'):
            if not isinstance(options[key],str) or len(options[key])>1024:raise ValueError('Невалиден път до LAN сертификата.')
        return options
    def start(self,p):
        with self.lock:
            if self.server:return self.status({})
            if p.get('values'):self.configure(p)
            self.options=self.validate(self.options,starting=True)
            server=RemoteServer(self.ctx,self.folder,dict(self.options),self.publish)
            try:server.start()
            except (OSError,ValueError) as error:
                server.stop();self.problem='HTTPS връзката не може да се включи: '+str(error);self.publish();raise ValueError(self.problem)
            self.options['dns']=server.tls.hostname
            self.server=server;self.problem='';
            if self.ctx.remote('desktop.available') and not self.tray:
                self.tray=Tray(self,lambda:self.ctx.remote('desktop',dict(action='show')),lambda:self.ctx.remote('desktop',dict(action='quit')));self.tray.start()
                if self.tray.problem:self.problem='Икона до часовника: '+self.tray.problem
            self.options['autostart']=True;self.ctx.save_settings(self.options);self.publish();return dict(message='Remote Operation е включен. Използвай целия адрес и кода за сдвояване.')
    def stop(self,p):
        with self.lock:server=self.server;self.server=None;self.options['autostart']=False;self.ctx.save_settings(self.options)
        if server:server.stop()
        self.publish();return dict(message='Връзката е спряна. Всички сесии са отнети.')
    def close(self):
        self.closed=True;server=self.server;self.server=None
        if server:server.stop()
        if self.tray:self.tray.stop();self.tray=None
    def status(self,p):return dict(running=bool(self.server),mode=self.options['mode'],url=self.primary(),lanUrl=self.lan_url(),vpnUrl=self.vpn_url(),devices=len(self.server.devices()) if self.server else 0,problem=self.problem,security=self.server.tls.status() if self.server else dict(secure=False))
    def primary(self):return self.vpn_url() if self.options['mode']=='vpn' else self.lan_url()
    def lan_url(self):return f"https://{self.options['dns']}:{self.options['port']}/" if self.options.get('dns') and self.options['mode']=='lan' else ''
    def vpn_url(self):return f"https://{self.options['dns']}:{self.options['port']}/" if self.options.get('dns') and self.options['mode']=='vpn' else ''
    def code(self,p):
        if not self.server:raise ValueError('Първо включи връзката.')
        self.server.new_code();self.publish();return dict(message='Създаден е нов еднократен код с валидност 10 минути.')
    def revoke(self,p):
        if self.server:self.server.revoke(p.get('selected',{}).get('id'))
        return dict(message='Достъпът е отнет.')
    def refresh(self,p):self.publish();return self.status(p)
    def copy(self,p):return dict(copyText=self.primary(),message='Адресът е копиран.')
    def publish(self):
        if self.closed:return
        with self.lock:
            on=bool(self.server);field=lambda kind,ident,label,**extra:dict(type=kind,id=ident,label=label,**extra)
            elements=[field('text','state','',value=('● Връзката е включена' if on else '● Връзката е изключена'),tone='success' if on else 'warning'),
                field('text','description','',value='Компютърът е хостът. Всички дистанционни връзки използват HTTPS с доверен сертификат. Смяната на мрежата става след спиране и прекратява всички сесии.'),
                field('select','mode','Начин на свързване',value=self.options['mode'],options=[dict(value='lan',label='Домашна мрежа · LAN / Wi-Fi'),dict(value='vpn',label='VPN · Tailscale')],command='configure',disabled=on),
                field('button','start','Включи връзката',command='start',disabled=on,tone='primary'),field('button','stop','Спри и изключи всички устройства',command='stop',disabled=not on),
                field('text','address','Основен адрес',value=self.primary() or 'Не е открит мрежов адрес.'),field('button','copy','Копирай основния адрес',command='copy'),
                field('text','lan-note','',visible=self.options['mode']=='lan',value='Директният LAN режим изисква DNS име и доверен PEM сертификат с ключ, предоставени от теб. Името трябва да сочи към компютъра и сертификатът да е доверен и на телефона. Без готов сертификат връзката не се включва. Tailscale HTTPS работи и у дома.'),
                field('input','lanAddress','Домашен IPv4 на компютъра',value=self.options['lanAddress'],command='configure',disabled=on,visible=self.options['mode']=='lan'),field('input','lanNetwork','Разрешена LAN подмрежа',value=self.options['lanNetwork'],command='configure',disabled=on,visible=self.options['mode']=='lan'),
                field('input','dns','DNS име за LAN сертификата',value=self.options['dns'],command='configure',disabled=on,visible=self.options['mode']=='lan'),
                field('input','certificateFile','LAN сертификат · пълен път до PEM веригата',value=self.options['certificateFile'],command='configure',disabled=on,visible=self.options['mode']=='lan'),
                field('input','privateKeyFile','LAN частен ключ · пълен път до PEM файла',value=self.options['privateKeyFile'],command='configure',disabled=on,visible=self.options['mode']=='lan'),
                field('text','vpn-note','',visible=self.options['mode']=='vpn',value='Отвори целия HTTPS адрес с DNS името и включен Tailscale на двете устройства. Работи през домашен Wi-Fi, мобилен интернет и друга мрежа. Сертификатът се получава и подновява автоматично чрез вече разрешения Tailscale HTTPS. Няма промени по VPN, рутера, Serve или публично публикуване.'),
                field('input','vpnAddress','Tailscale IPv4 на компютъра',value=self.options['vpnAddress'],command='configure',disabled=on,visible=self.options['mode']=='vpn'),
                field('input','vpnPeers','Разрешени Tailscale IPv4 адреси на устройствата',value=self.options['vpnPeers'],command='configure',disabled=on,visible=self.options['mode']=='vpn',help='Точни адреси, разделени със запетая. Например адресът на телефона в Tailscale.'),
                field('number','port','Постоянен TCP порт',value=self.options['port'],min=1024,max=65535,step=1,command='configure',disabled=on),
                field('number','uploadMb','Максимален размер на един файл [MB]',value=self.options['uploadMb'],min=1,max=1024,step=1,command='configure',disabled=on),
                field('checkbox','autostart','Включвай при стартиране на Astro Archive',value=self.options['autostart'],command='configure',disabled=on),field('button','refresh','Обнови състоянието',command='refresh')]
            if self.problem:elements.append(field('text','problem','',value=self.problem,tone='error'))
            if on:
                security=self.server.tls.status()
                elements.append(field('text','tls','',value='HTTPS · TLS 1.2 / 1.3 · сертификат до '+security['certificateExpires']+' · ключ, защитен с Windows DPAPI',tone='success' if security['secure'] else 'error'))
                if security['problem']:elements.append(field('text','tls-problem','',value=security['problem'],tone='warning'))
                code=self.server.code;elements.extend([field('text','code','',value='Код за сдвояване: '+code[:5]+'-'+code[5:]+' · еднократен, до 10 минути'),field('button','new-code','Нов код за сдвояване',command='code'),field('table','devices','Устройства · натисни ред, за да прекратиш сесията',command='revoke',columns=[dict(key='name',label='Устройство'),dict(key='address',label='Мрежов адрес'),dict(key='last',label='Последна връзка')],rows=self.server.devices()),field('button','revoke-all','Изключи всички сесии',command='revoke')])
            try:self.ctx.publish('connection',elements)
            except PermissionError:pass
            try:self.ctx.publish('link-status',[field('metric','remote-connection','Връзка със сървъра',value='Свързване…',tone='normal')])
            except PermissionError:pass

"""Example independent addition. No imports from application implementation files."""
from collections import defaultdict

def setup(context):
    def preferences():return dict(dict(title='Кадри по обекти',palette='blue',chart='bar',exposure=True),**context.settings())
    def overview(parameters):
        data=context.host('python.archive.query',{'filters':{'archive':'frames','kind':'frame'}})
        groups=defaultdict(lambda:dict(files=0,frames=0,exposure=0))
        for frame in data['rows']:
            row=groups[frame['object']];row['files']+=1;row['frames']+=frame.get('frames',1);row['exposure']+=frame.get('totalExposure') or 0
        return dict(rows=[dict(id=str(i),object=name,**value) for i,(name,value) in enumerate(sorted(groups.items()),1)],statistics=data['statistics'])
    def refresh(parameters):
        options=preferences();data=overview({});rows=data['rows'];color='#76beff' if options['palette']=='blue' else '#ae95ef'
        columns=[dict(key='object',label='Обект'),dict(key='files',label='Файлове',format='number'),dict(key='frames',label='Кадри',format='number')]
        if options['exposure']:columns.append(dict(key='exposure',label='Обща експозиция',format='exposure'))
        document=[dict(type='text',id='help',span=2,value='Това поле се създава от отделна папка в Plugins чрез фиксирани UI команди.'),
            dict(type='metric',id='objects',label='Обекти',value=len(rows)),dict(type='metric',id='frames',label='Кадри',value=data['statistics']['frames']),
            dict(type='table',id='table',label='Подробна справка',columns=columns,rows=rows),
            dict(type='chart',id='chart',label=options['title'],chartType=options['chart'],xLabel='Обект №',yLabel='Кадри',series=[dict(name='Брой кадри',color=color,points=[dict(x=i,y=row['frames'],label=row['object']) for i,row in enumerate(rows,1)])]),
            dict(type='button',id='refresh',label='Обнови от индекса',command='refresh')]
        context.publish('archive-summary',document);context.publish('analysis-summary',document)
        fields=[dict(type='input',id='title',label='Заглавие на диаграмата',value=options['title'],maxLength=120),
            dict(type='select',id='palette',label='Цвят',value=options['palette'],options=[dict(value='blue',label='Син'),dict(value='violet',label='Виолетов')]),
            dict(type='select',id='chart',label='Вид диаграма',value=options['chart'],options=[dict(value='bar',label='Колони'),dict(value='line',label='Линия'),dict(value='scatter',label='Точки')]),
            dict(type='checkbox',id='exposure',label='Показвай общата експозиция в таблицата',value=options['exposure']),
            dict(type='button',id='save',label='Запази настройките на добавката',command='save',tone='primary')]
        context.publish('options',fields);context.publish('visual-options',fields)
        return dict(message='Справката е обновена.')
    def save(parameters):
        context.save_settings(parameters['values']);refresh({});return dict(message='Настройките на добавката са запазени.')
    context.command('overview',overview);context.command('refresh',refresh);context.command('save',save)
    refresh({})

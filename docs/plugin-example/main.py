"""Standalone example. No imports from Backend, Bridge or the main program."""
def setup(context):
    state={'count':int(context.settings().get('count',10))}
    def publish():
        points=[{'x':i,'y':i*i} for i in range(state['count'])]
        context.publish('sample-panel',[
            {'type':'number','id':'count','label':'Брой точки','value':state['count'],'min':1,'max':100,'step':1},
            {'type':'button','id':'calculate','label':'Изчисли','command':'calculate','tone':'primary'},
            {'type':'metric','id':'total','label':'Точки','value':state['count']},
            {'type':'chart','id':'plot','label':'Примерна диаграма','chartType':'line','xLabel':'X','yLabel':'Y','series':[{'name':'y = x²','points':points}]},
            {'type':'table','id':'rows','label':'Примерни данни','columns':[{'key':'x','label':'X','format':'number'},{'key':'y','label':'Y','format':'number'}],'rows':points}
        ])
    def calculate(parameters):
        count=parameters.get('values',{}).get('count',state['count'])
        if type(count) not in (int,float) or int(count)!=count or not 1<=count<=100:raise ValueError('Броят е цяло число от 1 до 100.')
        state['count']=int(count);context.save_settings(state);publish()
        return {'message':'Данните са обновени.'}
    context.command('calculate',calculate);publish()
    return lambda:None

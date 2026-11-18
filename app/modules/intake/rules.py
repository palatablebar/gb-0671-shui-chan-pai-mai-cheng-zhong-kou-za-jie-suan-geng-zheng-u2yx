from .reader import read_source
FIELDS=['id','ticket','kind','weight','unit','fish','price','rate','version','replaces','paid']
TIME_FIELDS=['weighed_at','dealt_at','reweighed_at']
def apply(state):
    read_source(state,FIELDS,{'编号':'id'},extras=TIME_FIELDS)

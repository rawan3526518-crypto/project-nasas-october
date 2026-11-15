import uvicorn
if __name__=='__main__':
    print('افتح / Open: http://127.0.0.1:8765')
    uvicorn.run('backend.app:app',host='127.0.0.1',port=8765)

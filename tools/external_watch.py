"""Independent health probe: run outside the monitored server (cron or GitHub Actions)."""
import json
import os
import sys
import urllib.error
import urllib.request

def check(url,token='',chat='',timeout=12):
    if not url.startswith('https://'): raise ValueError('HTTPS URL required')
    healthy=False
    reason='no response'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Monitor-Watch/1.0'}),timeout=timeout) as response:
            payload=json.loads(response.read(4096))
            healthy=response.status==200 and payload.get('status')=='ok'
            reason='HTTP '+str(response.status)+' '+str(payload.get('status','unknown'))
    except (urllib.error.URLError,ValueError,TimeoutError,OSError) as exc:
        reason=type(exc).__name__
    if not healthy and token and chat:
        msg='Monitor external check failed: '+reason+' ('+url+')'
        data=json.dumps({'chat_id':chat,'text':msg}).encode('utf-8')
        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=data,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=timeout): pass
        except (urllib.error.URLError,OSError) as exc: print('Telegram delivery error: '+type(exc).__name__,file=sys.stderr)
    print('OK' if healthy else 'DOWN: '+reason)
    return healthy

if __name__=='__main__':
    url=os.environ.get('MONITOR_HEALTH_URL','')
    if not url: raise SystemExit('MONITOR_HEALTH_URL required')
    sys.exit(0 if check(url,os.environ.get('MONITOR_TELEGRAM_TOKEN',''),os.environ.get('MONITOR_TELEGRAM_CHAT_ID','')) else 1)

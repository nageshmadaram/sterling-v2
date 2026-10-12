"""Explicit provider probes. Never changes provider selection or submits orders."""
import json
import re
import time
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote as escape
import httpx
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from app.core.auth import UserContext,get_current_user
from app.services import db
from app.services.kite_engine.market_hours import session_phase
router=APIRouter(prefix='/feed-test',tags=['feed-test'])

@router.get('/search')
async def search(provider:str=Query(pattern='^(kite|yahoo|truedata)$'),query:str=Query(min_length=1,max_length=80),user:UserContext=Depends(get_current_user)):
    if provider=='yahoo':
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                response=await c.get('https://query1.finance.yahoo.com/v1/finance/search',params={'q':query,'quotesCount':12,'newsCount':0},headers={'User-Agent':'Mozilla/5.0'});response.raise_for_status()
            return {'instruments':[{'symbol':r['symbol'],'name':r.get('shortname') or r.get('longname') or r['symbol']} for r in response.json().get('quotes',[]) if r.get('symbol')]}
        except Exception:raise HTTPException(502,'Yahoo search is unavailable. Try entering the provider symbol directly.')
    from .kite import instruments
    try:
        result=await instruments('',query,12,user)
        return {'instruments':[{'symbol':f"{r.get('exchange','NSE')}:{r['tradingsymbol']}",'name':r.get('name') or r['tradingsymbol']} for r in result['instruments']]}
    except Exception:raise HTTPException(503,'Instrument search unavailable. Reconnect Kite or enter the provider symbol directly.')

class Probe(BaseModel):
    provider:str=Field(pattern='^(kite|yahoo|truedata)$')
    symbol:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9^.:_ &+-]+$')

@router.post('/probe')
async def probe(body:Probe,user:UserContext=Depends(get_current_user)):
    started=time.monotonic();now=datetime.now(ZoneInfo('Asia/Kolkata'));price=None;stamp=None
    try:
        if body.provider=='kite':
            from .kite import quote
            data=await quote([body.symbol],user);row=data.get(body.symbol) or {};price=row.get('last_price');stamp=row.get('last_trade_time') or row.get('timestamp')
        elif body.provider=='yahoo':
            async with httpx.AsyncClient(timeout=12) as c:
                r=await c.get('https://query1.finance.yahoo.com/v8/finance/chart/'+escape(body.symbol,safe=''),params={'interval':'1m','range':'1d'},headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
            result=(r.json().get('chart',{}).get('result') or [])[0];meta=result.get('meta') or {};price=meta.get('regularMarketPrice');stamp=meta.get('regularMarketTime')
        else:
            from .truedata import _require_active,truedata_service
            symbol=body.symbol.split(':')[-1];client=truedata_service.build_client(_require_active(user))
            try: rows=await client.get_ticks(symbol,(now-timedelta(minutes=20)).isoformat(),now.isoformat(),bidask=1)
            finally: await client.aclose()
            row=rows[-1] if rows else {};price=row.get('ltp') or row.get('price');stamp=row.get('timestamp') or row.get('time')
    except Exception:raise HTTPException(502,f'{body.provider} did not return a usable quote. Check the provider symbol, session and connectivity.')
    if isinstance(price,bool):raise HTTPException(502,'Provider returned no valid price')
    try:price=float(price)
    except (TypeError,ValueError):raise HTTPException(502,'Provider returned no valid price')
    if not 0<price<1e12:raise HTTPException(502,'Provider returned no valid price')
    observed=None
    try:
        observed=datetime.fromtimestamp(float(stamp)/(1000 if float(stamp)>1e12 else 1),tz=ZoneInfo('Asia/Kolkata')) if isinstance(stamp,(int,float)) else datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
        if observed.tzinfo is None:observed=observed.replace(tzinfo=ZoneInfo('Asia/Kolkata'))
    except Exception:pass
    age=(now-observed).total_seconds() if observed else None
    phase=session_phase(now,exchange='NFO');fresh=body.provider!='yahoo' and phase=='continuous' and age is not None and -5<=age<=30
    result={'id':str(uuid.uuid4()),'provider':body.provider,'symbol':body.symbol,'price':price,'quote_time':observed.isoformat() if observed else None,'observed_at':now.isoformat(),'age_seconds':round(age,1) if age is not None else None,'latency_ms':round((time.monotonic()-started)*1000),'market_phase':phase,'freshness':'verified recent quote' if fresh else 'delayed / historical' if age is not None else 'provider timestamp unavailable','realtime_verified':fresh,'note':'Yahoo may be delayed; a successful fetch does not verify tick-by-tick realtime data.' if body.provider=='yahoo' else 'No feed or execution settings were changed.'}
    db.set_config_strict('feed_probe:'+user.user_id,json.dumps(result))
    return result

class Confirmation(BaseModel):
    id:str
@router.post('/confirm')
def confirm(body:Confirmation,user:UserContext=Depends(get_current_user)):
    result=json.loads(db.get_config_strict('feed_probe:'+user.user_id,'{}'))
    if result.get('id')!=body.id:raise HTTPException(409,'Run a new test before confirming')
    if time.time()-datetime.fromisoformat(result['observed_at']).timestamp()>600:raise HTTPException(409,'Test result expired. Fetch a new quote.')
    db.set_config_strict('feed_confirmed:'+user.user_id,json.dumps(result))
    return {'confirmed':True,'result':result}

@router.get('/status')
def last_confirmation(user:UserContext=Depends(get_current_user)):
    return {'confirmed':json.loads(db.get_config_strict('feed_confirmed:'+user.user_id,'null'))}

'use strict';
let token='', state=null, lastEvent=0, toastTimer=null, polling=false,activePage='work',payoutDirty=false,historySignature='';
const $=id=>document.getElementById(id), I=window.PuzzleI18n, t=(key,args)=>I.t(key,args);
function toast(message,raw=''){$('toast').textContent=message;$('toast').title=raw;$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),5000)}
function localize(){document.documentElement.lang=I.language;document.querySelectorAll('[data-window]').forEach(b=>{b.title=t('window.'+b.dataset.window);b.setAttribute('aria-label',b.title)});document.title=I.language.startsWith('zh')?'KeyTrail · 钥迹':'KeyTrail';$('brand-local').classList.toggle('hidden',!I.language.startsWith('zh'));$('brand-local').textContent=I.language==='zh-TW'?'鑰跡':'钥迹';document.querySelectorAll('[data-i18n]').forEach(node=>node.textContent=t(node.dataset.i18n));$('language').value=I.language;$('refresh').title=t('refresh.requested');if(state)render(state)}
async function api(path,body){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Local-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){const value=await response.json();throw Error(value.error||t('link.lost'))}
 return (response.headers.get('Content-Type')||'').includes('application/json')?response.json():response.blob();
}
const number=value=>{try{return BigInt(value||0).toLocaleString(I.language)}catch{return Number(value||0).toLocaleString(I.language)}};
const btc=value=>value==null?'—':(Number(value)/1e8).toFixed(8);
function percent(checked,total){
 const n=BigInt(checked),d=BigInt(total);
 if(n===0n)return '0%';
 const ratio=Number(n)/Number(d)*100;
 if(ratio<1e-10)return ratio.toExponential(5)+'%';
 const precision=10n**14n,rounded=(n*100n*precision+d/2n)/d;
 let digits=(rounded%precision).toString().padStart(14,'0').replace(/0+$/,'');
 return (rounded/precision).toString()+(digits?'.'+digits:'')+'%';
}
function runtime(seconds){const s=Math.floor(seconds||0),h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=s%60;return(h?String(h).padStart(2,'0')+':':'')+String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0')}
function pill(id,text,color){$(id).textContent=text;$(id).className='pill '+color}
function setPage(page){
 activePage=page;
 for(const name of ['work','plan','results'])$('page-'+name).classList.toggle('hidden',name!==page);
 for(const [id,name] of [['nav-work','work'],['nav-plan','plan'],['nav-claim','results']]){if(name===page)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current')}
}
function openPlan(){setPage('plan');if(state){$('range-mode').value=state.config.schema===2?'custom':'full';$('range-input-start').value=state.selected_range?.[0]||'400000000000000000';$('range-input-end').value=state.selected_range?.[1]||'7FFFFFFFFFFFFFFFFF';$('total-computers').value=state.config.total}}
function openClaim(){setPage('results');renderClaim()}
function renderClaim(){
 if(!state)return;const c=state.claim;
 $('claim-title').textContent=t('claim.'+c.state);
 $('claim-details').classList.toggle('hidden',c.state==='NONE');
 $('claim-message').classList.toggle('hidden',c.state==='NONE');
 $('claim-message').textContent=c.state==='NONE'?t('claim.none.message'):c.state==='READY'?t('claim.ready.message'):c.state==='FOUND'?t('claim.found.message'):I.legacy(c.message);
 $('claim-message').title=c.message;
 $('claim-address').textContent=state.payout;$('claim-amount').textContent=btc(c.amount_sats)+' BTC';$('claim-fee').textContent=btc(c.fee_sats)+' BTC';
 $('prepare-claim').disabled=['NONE','SENDING','SUBMITTED','SUBMISSION_UNKNOWN','CONFIRMED'].includes(c.state);$('export-claim').disabled=!['READY','SUBMITTED','CONFIRMED'].includes(c.state);$('review-broadcast').disabled=c.state!=='READY';
 $('refresh-receipt').disabled=!['SUBMITTED','SUBMISSION_UNKNOWN'].includes(c.state);
 $('claim-txid').textContent=c.txid||'';$('claim-txid').href=c.txid?'https://mempool.space/tx/'+c.txid:'#';
 $('auto-state').textContent=t(c.auto_armed?'auto.on':'auto.off');$('auto-state').className=c.auto_armed?'armed':'';
 if(!payoutDirty){$('payout-input').value=state.payout;$('auto-receive').checked=!!c.auto_armed}
 $('auto-details').classList.toggle('hidden',!$('auto-receive').checked||!!c.auto_armed&&!payoutDirty);
}
function render(s){
 $('app-version').textContent='v'+s.version;
 $('page-work').dataset.runState=s.run_state;
 state=s;const r=s.keys_per_second;
 if(r>=1e6){$('rate-value').textContent=(r/1e6).toLocaleString(I.language,{minimumFractionDigits:2,maximumFractionDigits:2});$('rate-unit').textContent=t('unit.mkeys')}
 else{$('rate-value').textContent=r?number(Math.round(r)):'—';$('rate-unit').textContent=t('unit.keys')}
 const device=s.hardware.name||'';
 $('engine').textContent=(device?'':I.engine(s.backend))+(device?device:'');$('engine').title=I.engine(s.backend);
 $('temperature').textContent=s.hardware.temperature?Number(s.hardware.temperature).toFixed(1)+' °C':'— °C';
 pill('run-pill',t('run.'+s.run_state),s.running?'green':s.run_state==='FOUND'?'amber':'neutral');
 $('run-reason').textContent=I.legacy(s.reason)+(s.remaining_seconds!=null?' · '+t('remaining',{time:runtime(s.remaining_seconds)}):'');$('run-reason').title=s.reason;
 $('checked').textContent=number(s.progress.checked);$('runtime').textContent=runtime(s.progress.runtime);
 const em=document.createElement('em');em.textContent='/ '+String(s.config.total).padStart(2,'0');$('node-number').replaceChildren(document.createTextNode(String(s.config.index).padStart(2,'0')+' '),em);
 $('plan-short').textContent=s.plan_ready?s.config.plan_id.slice(0,8).toUpperCase():'—';
 $('current-plan').textContent=t('plan.current',{plan:s.config.plan_id.slice(0,8).toUpperCase(),index:s.config.index,total:s.config.total});
 $('range-start').textContent=s.progress.start_hex.slice(2).toUpperCase();$('range-next').textContent=s.progress.next_hex.slice(2).toUpperCase();$('range-end').textContent=s.progress.end_hex.slice(2).toUpperCase();
 const assigned=BigInt(s.progress.end_hex)-BigInt(s.progress.start_hex)+1n;
 const whole=s.total_keyspace||String(1n<<70n);
 const unique=s.total_checked||s.progress.checked;
 $('total-coverage').textContent=percent(unique,whole);
 $('total-coverage').title=number(unique)+' / '+number(whole);
 $('progress-bar').style.width=Math.min(100,Number(unique)/Number(whole)*100)+'%';
 $('progress-text').textContent=t('coverage.assigned',{percent:percent(s.progress.checked,assigned)});
 $('progress-text').classList.toggle('hidden',s.config.total===1&&assigned===BigInt(whole));
 $('start').classList.toggle('hidden',s.running);$('stop').classList.toggle('hidden',!s.running);$('duration').disabled=s.running;$('backend').disabled=s.running;$('payout').textContent=s.payout||t('payout.notset');$('copy-payout').disabled=!s.payout;
 const m=s.monitor;const label=t('monitor.'+m.state);
 pill('monitor-pill',label,m.state==='READY'?'green':m.state==='SPENT'?'red':'amber');
 $('balance').textContent=btc(m.balance_sats);$('monitor-message').textContent=label;$('monitor-message').title=I.legacy(m.message);
 $('sources').textContent=t('sources',{n:m.sources_ok});$('freshness').textContent=m.last_good?t('freshness',{n:Math.max(0,Math.floor(Date.now()/1000-m.last_good))}):'—';
 const q=s.price||{},price=Number(q.usd_per_btc),available=Number.isFinite(price)&&price>0&&m.balance_sats!=null;
 $('balance-usd').textContent=available?'≈ '+new Intl.NumberFormat(I.language,{style:'currency',currency:'USD',currencyDisplay:'narrowSymbol',maximumFractionDigits:0}).format(Number(m.balance_sats)/1e8*price)+' USD':'≈ — USD';
 $('balance-usd').title=q.updated_at?'mempool.space · '+new Date(q.updated_at*1000).toLocaleString(I.language):'';
 const stale=q.state==='STALE'||q.updated_at&&Date.now()/1000-q.updated_at>3600;
 $('quote-state').classList.toggle('hidden',available&&!stale);$('quote-state').textContent=t(stale?'quote.stale':'quote.unavailable');
 const critical=['SPENT','SPEND_DETECTED'].includes(m.state)||s.run_state==='FOUND';
 $('critical-banner').classList.toggle('hidden',!critical);$('critical-banner').textContent=s.run_state==='FOUND'?t('claim.found.message'):label;
 $('events').replaceChildren();
 for(const event of s.events){
  const row=document.createElement('div');row.className='event '+event.kind;
  const time=document.createElement('time');time.textContent=new Date(event.at*1000).toLocaleTimeString(I.language,{hour12:false});
  const kind=document.createElement('span');kind.className='kind';kind.textContent=t('kind.'+event.kind);
  const message=document.createElement('span');message.className='message';message.textContent=I.legacy(event.message);message.title=event.message;
  row.append(time,kind,message);$('events').append(row);
  if(lastEvent&&event.id>lastEvent&&['alert','found'].includes(event.kind)&&'Notification'in window&&Notification.permission==='granted')new Notification('KeyTrail',{body:I.legacy(event.message),icon:'/icon.svg'});
 }
 if(s.events.length)lastEvent=Math.max(lastEvent,...s.events.map(e=>e.id));
 renderClaim();
 const signature=JSON.stringify(s.saved_plans||[]);
 if(signature!==historySignature){const old=$('saved-range').value;$('saved-range').replaceChildren();for(const run of s.saved_plans||[]){const option=document.createElement('option');option.value=JSON.stringify(run.config);option.textContent=run.config.plan_id.slice(0,8).toUpperCase()+' · '+number(run.checked);$('saved-range').append(option)}if([...$('saved-range').options].some(o=>o.value===old))$('saved-range').value=old;historySignature=signature}
}
async function poll(){if(polling)return;polling=true;try{render(await api('/api/state'))}catch(error){$('page-work').dataset.runState='DISCONNECTED';pill('run-pill',t('link.lost'),'amber')}finally{polling=false}}
async function action(path,body={}){try{await api(path,body);await poll();return true}catch(error){toast(I.legacy(error.message),error.message);return false}}
function download(blob,name){const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),5000)}
$('language').addEventListener('change',async event=>{const chosen=event.target.value;I.setLanguage(chosen);localize();await action('/api/preferences',{language:chosen})});
$('start').addEventListener('click',()=>{if(!state?.payout){openClaim();toast(t('payout.required'));return}if(!state?.plan_ready){openPlan();return}action('/api/start',{duration:Number($('duration').value),backend:$('backend').value})});
$('stop').addEventListener('click',()=>action('/api/stop'));
$('refresh').addEventListener('click',()=>{action('/api/refresh');toast(t('refresh.requested'))});
for(const id of ['open-plan','nav-plan'])$(id).addEventListener('click',openPlan);
for(const id of ['open-claim','nav-claim'])$(id).addEventListener('click',openClaim);
$('nav-work').addEventListener('click',()=>setPage('work'));
$('notifications').addEventListener('click',async()=>{if(!('Notification'in window)){toast(t('notify.off'));return}const permission=await Notification.requestPermission();toast(t(permission==='granted'?'notify.on':'notify.off'))});
$('copy-payout').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(state.payout);toast(t('copy.done'))}catch(error){toast(I.legacy(error.message),error.message)}});
$('generate-plan').addEventListener('click',async()=>{
 try{const total=Number($('total-computers').value);if(!Number.isInteger(total)||total<1||total>1024)throw Error(t('plan.invalid'));
 if(state.plan_ready&&!confirm(t('plan.confirm')))return;
 const begin=$('range-input-start').value.trim().replace(/^0x/i,''),end=$('range-input-end').value.trim().replace(/^0x/i,'');
 if(!/^[0-9a-f]+$/i.test(begin)||!/^[0-9a-f]+$/i.test(end)||BigInt('0x'+begin)>BigInt('0x'+end)||BigInt('0x'+begin)<(1n<<70n)||BigInt('0x'+end)>((1n<<71n)-1n))throw Error(t('range.invalid'));
 const blob=await api('/api/plan',{total,range_start:begin,range_end:end});download(blob,'Puzzle71-computers.zip');await poll();toast(t('plan.created'))}
 catch(error){toast(I.legacy(error.message),error.message)}
});
$('import-plan').addEventListener('click',()=>$('import-file').click());
$('import-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>16384)throw Error(t('file.large'));await api('/api/config/import',JSON.parse(await file.text()));await poll();openPlan();toast(t('plan.imported'))}catch(error){toast(I.legacy(error.message),error.message)}event.target.value=''});
$('prepare-claim').addEventListener('click',()=>action('/api/claim/prepare'));
$('export-claim').addEventListener('click',async()=>{try{download(await api('/api/claim/export'),'signed-transaction.txt')}catch(error){toast(I.legacy(error.message),error.message)}});
$('review-broadcast').addEventListener('click',()=>$('broadcast-form').classList.toggle('hidden'));
$('broadcast').addEventListener('click',async()=>{
 try{if(!$('race-risk').checked)throw Error(t('ack.required'));if($('confirm-address').value.trim()!==state.payout)throw Error(t('address.mismatch'));if(!confirm(t('broadcast.confirm')))return;
 $('broadcast').disabled=true;await api('/api/claim/broadcast',{nonce:state.claim.nonce,confirmation:$('confirm-address').value.trim(),acknowledge_race_risk:true});toast(t('claim.SUBMITTED'));await poll()}
 catch(error){toast(I.legacy(error.message),error.message)}finally{$('broadcast').disabled=false}
});
$('quit').addEventListener('click',async()=>{if(confirm(t('quit.confirm'))){await action('/api/quit');toast(t('quit.done'))}});
$('range-mode').addEventListener('change',event=>{
 const begin=1n<<70n,end=(1n<<71n)-1n,quarter=(1n<<70n)/4n;let a=begin,b=end;
 if(event.target.value==='custom')return;
 if(event.target.value==='remaining'){a=BigInt(state.progress.next_hex);b=BigInt(state.progress.end_hex)}
 if(/^q[1-4]$/.test(event.target.value)){const n=BigInt(event.target.value[1])-1n;a=begin+n*quarter;b=a+quarter-1n}
 $('range-input-start').value=a.toString(16).toUpperCase();$('range-input-end').value=b.toString(16).toUpperCase();
});
for(const id of ['range-input-start','range-input-end'])$(id).addEventListener('input',()=>$('range-mode').value='custom');
$('load-range').addEventListener('click',async()=>{try{const saved=JSON.parse($('saved-range').value);if(state.running)throw Error(t('stop.first'));if(await action('/api/config/import',saved)){openPlan();toast(t('saved'))}}catch(error){toast(I.legacy(error.message),error.message)}});
$('payout-input').addEventListener('input',()=>{payoutDirty=true;$('auto-confirm-address').value='';$('auto-risk').checked=false;renderClaim()});
$('auto-receive').addEventListener('change',()=>{payoutDirty=true;renderClaim()});
$('save-payout').addEventListener('click',async()=>{
 const payout=$('payout-input').value.trim(),auto=$('auto-receive').checked;
 if(auto&&($('auto-confirm-address').value.trim()!==payout||!$('auto-risk').checked)){toast(t('ack.required'));return}
 $('save-payout').disabled=true;
 try{await api('/api/payout',{payout,auto,confirmation:$('auto-confirm-address').value.trim(),acknowledge_race_risk:$('auto-risk').checked});payoutDirty=false;await poll();$('auto-confirm-address').value='';$('auto-risk').checked=false;toast(t('payout.saved'))}catch(error){toast(I.legacy(error.message),error.message)}finally{$('save-payout').disabled=false}
});
$('refresh-receipt').addEventListener('click',()=>action('/api/claim/receipt'));
localize();
(async()=>{try{const response=await fetch('/api/bootstrap');if(!response.ok)throw Error(t('link.lost'));const value=await response.json();token=value.token;I.setLanguage(value.state.language||'en');state=value.state;localize();$('duration').value=String(value.state.duration);setInterval(poll,1500)}catch(error){toast(I.legacy(error.message),error.message)}})();

window.addEventListener('pywebviewready',()=>{document.body.classList.add('native-window');$('window-controls').classList.remove('hidden')});
for(const button of document.querySelectorAll('[data-window]'))button.addEventListener('click',()=>window.pywebview?.api[button.dataset.window]());
document.querySelector('.header-drag').addEventListener('dblclick',()=>window.pywebview?.api.maximize());

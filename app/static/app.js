const $ = id => document.getElementById(id);
const money = n => new Intl.NumberFormat('en-GB', {style:'currency',currency:'USD',maximumFractionDigits:2}).format(n || 0);
const num = (n,d=5) => Number(n || 0).toLocaleString('en-GB',{maximumFractionDigits:d});
let timer, positions = [], refreshing = false;
async function j(url,opt={}) {
 const r = await fetch(url,{...opt,headers:{'Content-Type':'application/json','X-Apex-Request':'1',...opt.headers}});
 const data=await r.json();
 if(r.status===401 && url!=='/api/login') showLogin();
 if(!r.ok) throw new Error(typeof data.detail==='string'?data.detail:'Check the values and try again.');
 return data;
}
function showLogin(){clearTimeout(timer);$('dashboard').hidden=true;$('loginPanel').hidden=false;}
function drawChart(history){
 const svg=$('equityChart');svg.replaceChildren();
 if(history.length<2){$('chartMsg').hidden=false;return;}$('chartMsg').hidden=true;
 const values=history.map(p=>p.equity),min=Math.min(...values),max=Math.max(...values),range=Math.max(max-min,1);
 const points=values.map((v,i)=>`${10+i*780/(values.length-1)},${145-(v-min)*125/range}`).join(' ');
 const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');
 line.setAttribute('points',points);line.setAttribute('fill','none');line.setAttribute('stroke','#66e3c4');line.setAttribute('stroke-width','2');svg.append(line);
 const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`Equity from ${money(values[0])} to ${money(values.at(-1))}; range ${money(min)} to ${money(max)}`;svg.append(title);
}
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
 const [m,a,p,t,h,health]=await Promise.all([j('/api/market'),j('/api/account'),j('/api/positions'),j('/api/trades?limit=50'),j('/api/history'),j('/health')]);positions=p;
 $('equity').textContent=money(a.equity);$('cash').textContent=money(a.cash);$('unreal').textContent=money(a.unrealised_pnl);$('realised').textContent=money(a.realised_pnl);
 for(const [id,v] of [['unreal',a.unrealised_pnl],['realised',a.realised_pnl]])$(id).className=v>=0?'positive':'negative';
 $('markets').innerHTML=m.assets.map(x=>`<div class="market"><div><b>${x.symbol}</b><small>${x.name}</small></div><div class="market-price">${num(x.price,x.price<10?5:2)}<small>USD · simulated</small></div></div>`).join('');
 if(!$('symbol').options.length)$('symbol').innerHTML=m.assets.map(x=>`<option value="${x.symbol}">${x.symbol} — ${x.name}</option>`).join('');
 $('positions').innerHTML=p.length?p.map(x=>`<tr><td>${x.symbol}</td><td>${num(x.qty,8)}</td><td>${num(x.avg_price)}</td><td>${num(x.mark_price)}</td><td>${num(x.stop_loss)}</td><td class="${x.unrealised_pnl>=0?'positive':'negative'}">${money(x.unrealised_pnl)}</td><td><button class="secondary close-position" data-symbol="${x.symbol}" type="button">Close ${x.symbol}</button></td></tr>`).join(''):'<tr><td colspan="7">No positions yet. Place a paper buy to get started.</td></tr>';
 $('trades').innerHTML=t.length?t.map(x=>`<tr><td>${new Date(x.ts).toLocaleString()}</td><td>${x.symbol}<small>${x.source}</small></td><td class="${x.side}">${x.side.toUpperCase()}</td><td>${num(x.qty,8)}</td><td>${num(x.price)}</td><td class="${x.realised_pnl>=0?'positive':'negative'}">${money(x.realised_pnl)}</td></tr>`).join(''):'<tr><td colspan="6">Your filled orders will appear here.</td></tr>';
 $('dailyStatus').textContent=a.daily_risk.halted?'Daily loss limit reached: buys paused until the next UTC day. You can still close positions.':`Today’s equity loss: ${num(a.daily_risk.loss_pct*100,2)}%. Resets at midnight UTC.`;
 $('connection').textContent=health.engine_running?'● Simulation running':'Simulation stopped';$('appMsg').textContent='';drawChart(h);
 }catch(err){$('connection').textContent='Connection interrupted';$('appMsg').textContent=err.message;}finally{refreshing=false;}
}
async function poll(){await refresh();if(!$('dashboard').hidden)timer=setTimeout(poll,3000);}
async function loadSettings(){const s=await j('/api/settings');$('risk').value=s.risk_per_trade*100;$('maxPos').value=s.max_position_pct*100;$('lossLimit').value=s.daily_loss_limit_pct*100;$('autoTrade').checked=s.auto_trade;}
async function start(){const s=await j('/api/session');if(!s.authenticated){showLogin();return;}$('loginPanel').hidden=true;$('dashboard').hidden=false;$('logout').hidden=!s.password_required;await loadSettings();clearTimeout(timer);await poll();}
async function submitOrder(order){const data=await j('/api/orders',{method:'POST',body:JSON.stringify(order)});$('orderMsg').textContent=`Filled ${data.side} ${num(data.qty,8)} ${data.symbol} @ ${num(data.price)}`;await refresh();}
$('orderForm').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await submitOrder({symbol:$('symbol').value,side:$('side').value,qty:Number($('qty').value),stop_loss:$('side').value==='buy'&&$('stopLoss').value?Number($('stopLoss').value):null});}catch(err){$('orderMsg').textContent=err.message;}finally{b.disabled=false;}});
$('side').addEventListener('change',()=>{$('stopLoss').disabled=$('side').value==='sell';});
$('positions').addEventListener('click',async e=>{const b=e.target.closest('.close-position');if(!b)return;const p=positions.find(x=>x.symbol===b.dataset.symbol);if(!p)return;b.disabled=true;try{await submitOrder({symbol:p.symbol,side:'sell',qty:p.qty});}catch(err){$('appMsg').textContent=err.message;}finally{b.disabled=false;}});
$('settingsForm').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await j('/api/settings',{method:'PUT',body:JSON.stringify({risk_per_trade:Number($('risk').value)/100,max_position_pct:Number($('maxPos').value)/100,daily_loss_limit_pct:Number($('lossLimit').value)/100,auto_trade:$('autoTrade').checked})});$('settingsMsg').textContent='Risk settings saved.';await loadSettings();await refresh();}catch(err){$('settingsMsg').textContent=err.message;}finally{b.disabled=false;}});
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await j('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});$('password').value='';$('loginMsg').textContent='';await start();}catch(err){$('loginMsg').textContent=err.message;}finally{b.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await j('/api/logout',{method:'POST'});showLogin();}catch(err){$('appMsg').textContent=err.message;}});
start().catch(err=>{$('dashboard').hidden=false;$('appMsg').textContent=err.message;});

// Classic script: sandboxed pages have an opaque origin, and some reverse
// proxies omit the CORS headers required by external ES modules.
(async () => {
'use strict';
const bridge=window.AstrBotPluginPage;
const $=id=>document.getElementById(id);
const labels={reply_received:'收到投喂',reply_multiple:'收到多张图片（追加提示）',reply_pending:'同一用户已有任务',reply_busy:'队列已满',reply_cooldown:'投喂间隔过短',reply_malicious:'恶意投喂兜底回复',reply_non_food:'非食物兜底回复',reply_done:'完成投喂兜底回复'};
let state,dirty=false,busy=false,loaded=false;
function ask(message){return new Promise(resolve=>{const dialog=$('confirm-dialog');$('confirm-message').textContent=message;const finish=value=>{dialog.close();resolve(value)};$('confirm-cancel').onclick=()=>finish(false);$('confirm-accept').onclick=()=>finish(true);dialog.oncancel=e=>{e.preventDefault();finish(false)};dialog.showModal()})}
function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}
function controls(){ $('fields').disabled=busy||!loaded;$('save').disabled=busy||!loaded||!dirty||probabilityInvalid();$('reload').disabled=busy; }
function changed(){dirty=true;status('有未保存的修改');controls();counts()}
function probabilityInvalid(){return loaded && state.settings.steal_mode==='custom' && state.settings.guest_roles.filter(r=>r.enabled&&r.random_enabled).reduce((n,r)=>n+Number(r.probability||0),0)>100+1e-9}
function counts(){for(const key of ['character_prompt','review_prompt'])document.querySelector(`[data-count="${key}"]`).textContent=`${$(key).value.length} / ${key==='guest_persona'?6000:4000}`}
for(const [key,label] of Object.entries(labels)){const box=document.createElement('div'),l=document.createElement('label'),t=document.createElement('textarea');l.htmlFor=key;l.textContent=label;t.id=key;t.rows=3;t.maxLength=300;box.append(l,t);$('replies').append(box)}
for(const [key,label] of [['chibi_reference','主参考图（必填）'],['turnaround_reference','辅助参考图（可选）']]){
 const box=document.createElement('div'),l=document.createElement('label'),input=document.createElement('input'),name=document.createElement('p'),remove=document.createElement('button');
 l.textContent=label;l.htmlFor=key;input.id=key;input.type='file';input.accept='image/png,image/jpeg,image/webp';name.id=key+'-state';name.className='file-state';remove.type='button';remove.className='secondary';remove.textContent='移除';
 remove.onclick=()=>{state.settings[key]=[];input.value='';renderFiles();changed()};
 input.onchange=async()=>{const file=input.files[0];if(!file)return;if(file.size>10*1024*1024){status('图片不能超过10MB。',true);input.value='';return}busy=true;controls();status('正在上传图片…');try{const result=await bridge.upload('reference/'+key,file);state.settings[key]=[result.path];renderFiles();changed();status('图片已上传，点击“保存修改”后生效。')}catch(e){status('上传失败：'+e.message,true)}finally{input.value='';busy=false;controls()}};
 box.append(l,name,input,remove);$('references').append(box);
}
function renderFiles(){for(const key of ['chibi_reference','turnaround_reference'])$(key+'-state').textContent=state.settings[key]?.length?'已选择：'+state.settings[key][0].split('/').pop():'尚未选择图片'}
function render(){for(const key of ['character_prompt','review_prompt',...Object.keys(labels)])$(key).value=state.settings[key]||'';renderFiles();counts();renderGuests()}
async function load(){busy=true;controls();status('正在载入…');try{state=await bridge.apiGet('customization');render();dirty=false;loaded=true;status('已载入，修改后请保存。')}catch(e){status('载入失败：'+e.message,true)}finally{busy=false;controls()}}
$('editor').addEventListener('input',e=>{if(e.target.tagName==='TEXTAREA')changed()});
$('defaults').onclick=async()=>{if(!await ask('将固定回复重置为通用文案？保存后生效，提示词和参考图不会改变。'))return;for(const key of Object.keys(labels))$(key).value=state.defaults[key];changed()};
$('reload').onclick=async()=>{if(!dirty||await ask('重新载入会丢弃尚未保存的修改，是否继续？'))load()};
$('editor').onsubmit=async e=>{e.preventDefault();if(busy||!loaded)return;const settings={...state.settings};for(const key of ['character_prompt','review_prompt',...Object.keys(labels)])settings[key]=$(key).value;busy=true;controls();status('正在保存…');try{state=await bridge.apiPost('customization/save',{revision:state.revision,settings});render();dirty=false;status('已保存，对后续投喂生效。')}catch(e){status('保存失败：'+e.message,true)}finally{busy=false;controls()}};
function complete(r){const v=state.guest_reference_validity?.[r.id];return r.persona.trim() && r.reference.length===1 && v?.reference!==false && v?.secondary!==false}
function weights(){
 const s=state.settings, eligible=s.guest_roles.filter(r=>r.enabled&&r.random_enabled&&complete(r));
 return new Map(eligible.map(r=>[r.id,s.steal_mode==='average'?Number(s.steal_probability)/eligible.length:Number(r.probability)]));
}
function preview(){
 const s=state.settings,w=weights(),list=$('probability-preview');list.replaceChildren();
 const total=[...w.values()].reduce((a,b)=>a+b,0),configured=s.guest_roles.filter(r=>r.enabled&&r.random_enabled).reduce((a,r)=>a+Number(r.probability),0);
 const fmt=n=>Number(n.toFixed(2))+'%';
 const row=text=>{const p=document.createElement('p');p.textContent=text;list.append(p)};
 row('原角色：'+fmt(s.steal_enabled?Math.max(0,100-total):100));
 for(const r of s.guest_roles)row(`${r.name||'未命名角色'}：${fmt(s.steal_enabled?(w.get(r.id)||0):0)}${!r.enabled?'（已停用）':!complete(r)?'（请补齐人格，并检查参考图是否有效）':!r.random_enabled?'（仅管理员指定）':''}`);
 if(!w.size)row('暂无可随机出场的角色，普通投喂使用原角色。');
 if(s.steal_mode==='custom')row('当前抢饭总概率：'+fmt(s.steal_enabled?total:0));
 $('probability-error').textContent=probabilityInvalid()?`抢饭概率合计为${fmt(configured)}，超过上限${fmt(configured-100)}。请降低部分角色的概率后保存。`:'';
 $('mode-help').textContent=s.steal_mode==='average'?'只设总概率，各角色平分机会。例如总概率30%，三个角色各10%，原角色70%。添加或停用角色后自动重新平分。':'直接填写每个角色在全部正常投喂中的出场概率，剩余机会归原角色。例如15%＋10%＋5%，原角色就是70%。停用角色释放的概率归还原角色，其他角色不变。';
 $('total-field').hidden=s.steal_mode!=='average';controls();
}
function uniqueName(base){let name=base,n=2;while(state.settings.guest_roles.some(r=>r.name===name))name=base+n++;return name}
function newId(){return 'guest-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10)}
function renderGuests(){
 const s=state.settings;
 $('steal_enabled').checked=s.steal_enabled;$('steal_mode').value=s.steal_mode;$('steal_probability').value=s.steal_probability;
 $('guest-list').replaceChildren();
 for(const r of s.guest_roles){
  const card=document.createElement('details');card.className='guest-card';
  const title=document.createElement('summary');title.textContent=r.name||'新角色';card.append(title);
  function field(key,label,type='text',limit){
   const id=r.id+'-'+key,l=document.createElement('label'),input=document.createElement(type==='textarea'?'textarea':'input');
   l.htmlFor=id;l.textContent=label;input.id=id;
   if(type==='textarea'){input.rows=key==='persona'?9:key==='notice'?3:5;input.maxLength=limit}
   else input.type=type;
   if(type==='checkbox')input.checked=r[key];else input.value=r[key];
   if(type==='number'){input.min=0;input.max=100;input.step='any'}
   if(key==='name')input.maxLength=40;
   input.oninput=()=>{r[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value;if(key==='name')title.textContent=r.name||'未命名角色';changed();preview()};
   card.append(l,input);return input;
  }
  field('name','角色名称（用于指令，不能重复或包含空格）');
  field('enabled','启用此角色','checkbox');field('random_enabled','参与随机出场（关闭后仅供管理员指定）','checkbox');
  if(s.steal_mode==='custom')field('probability','每次投喂的出场概率（%，0表示仅指定测试）','number');
  for(const [key,label] of [['reference','主参考图（必填）'],['secondary','辅助参考图（可选）']]){
   const l=document.createElement('label'),input=document.createElement('input'),name=document.createElement('p'),remove=document.createElement('button');
   input.id=r.id+'-'+key;input.type='file';input.accept='image/png,image/jpeg,image/webp';l.htmlFor=input.id;l.textContent=label;
   name.textContent=r[key].length?'已选择：'+r[key][0].split('/').pop():'尚未选择图片';remove.type='button';remove.className='secondary';remove.textContent='移除图片';
   remove.onclick=()=>{r[key]=[];if(state.guest_reference_validity?.[r.id])state.guest_reference_validity[r.id][key]=key==='secondary';name.textContent='尚未选择图片';changed();preview()};
   input.onchange=async()=>{const file=input.files[0];if(!file)return;if(file.size>10*1024*1024){status('图片不能超过10MB。',true);input.value='';return}busy=true;controls();try{const out=await bridge.upload('reference/guest_reference',file);r[key]=[out.path];if(state.guest_reference_validity?.[r.id])state.guest_reference_validity[r.id][key]=true;name.textContent='已选择：'+file.name;changed();preview();status('图片已上传，点击保存后生效。')}catch(e){status('上传失败：'+e.message,true)}finally{input.value='';busy=false;controls()}};
   card.append(l,name,input,remove);
  }
  field('notice','抢饭提醒（最多300字，留空不发送）','textarea',300);
  const noticeHelp=document.createElement('p');noticeHelp.textContent='生图完成后先发送这句提醒，紧接着另发一条点评与图片。不会在排队或生图时提前提醒。';card.append(noticeHelp);
  field('persona','独立人格（最多6000字）','textarea',6000);field('character_prompt','外观补充（最多4000字）','textarea',4000);
  const actions=document.createElement('div');actions.className='actions';
  for(const [label,action] of [['复制角色',()=>{if(s.guest_roles.length>=30){status('最多添加30个角色。',true);return}const copy=JSON.parse(JSON.stringify(r));copy.id=newId();copy.name=uniqueName(r.name+'副本');copy.probability=0;s.guest_roles.push(copy);changed();renderGuests()}],['删除角色',async()=>{if(await ask('删除“'+r.name+'”？保存后生效；已上传图片不会立即删除。')){s.guest_roles=s.guest_roles.filter(x=>x.id!==r.id);changed();renderGuests()}}]]){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;b.onclick=action;actions.append(b)}
  card.append(actions);$('guest-list').append(card);
 }
 preview();
}
$('add-guest').onclick=()=>{if(state.settings.guest_roles.length>=30){status('最多添加30个角色。',true);return}state.settings.guest_roles.push({id:newId(),name:uniqueName('新角色'),enabled:true,random_enabled:true,probability:0,reference:[],secondary:[],persona:'',character_prompt:'',notice:state.guest_notice_default||'刚递过去，就被半路接走了……'});changed();renderGuests();const cards=$('guest-list').children;cards[cards.length-1].open=true};
$('steal_enabled').onchange=()=>{state.settings.steal_enabled=$('steal_enabled').checked;changed();preview()};
$('steal_probability').oninput=()=>{state.settings.steal_probability=Number($('steal_probability').value);changed();preview()};
$('steal_mode').onchange=()=>{const s=state.settings,w=weights(),mode=$('steal_mode').value;if(mode==='custom'){for(const r of s.guest_roles)r.probability=w.get(r.id)||0}else{s.steal_probability=[...w.values()].reduce((a,b)=>a+b,0)}s.steal_mode=mode;changed();renderGuests()};

window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
try{if(!bridge)throw new Error('请从 AstrBot 插件页面打开此页面');const context=await bridge.ready();document.body.classList.toggle('dark',!!context.isDark);bridge.onContext(c=>document.body.classList.toggle('dark',!!c.isDark));await load()}catch(e){status(e.message,true)}
})();

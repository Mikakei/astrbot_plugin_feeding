const bridge=window.AstrBotPluginPage;
const $=id=>document.getElementById(id);
const labels={reply_received:'收到投喂',reply_multiple:'收到多张图片（追加提示）',reply_pending:'同一用户已有任务',reply_busy:'队列已满',reply_cooldown:'投喂间隔过短',reply_malicious:'恶意投喂兜底回复',reply_non_food:'非食物兜底回复',reply_done:'完成投喂兜底回复'};
let state,dirty=false,busy=false,loaded=false;
function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}
function controls(){ $('fields').disabled=busy||!loaded;$('save').disabled=busy||!loaded||!dirty;$('reload').disabled=busy; }
function changed(){dirty=true;status('有未保存的修改');controls();counts()}
function counts(){for(const key of ['character_prompt','review_prompt'])document.querySelector(`[data-count="${key}"]`).textContent=`${$(key).value.length} / 4000`}
for(const [key,label] of Object.entries(labels)){const box=document.createElement('div'),l=document.createElement('label'),t=document.createElement('textarea');l.htmlFor=key;l.textContent=label;t.id=key;t.rows=3;t.maxLength=300;box.append(l,t);$('replies').append(box)}
for(const [key,label] of [['chibi_reference','主参考图（必填）'],['turnaround_reference','辅助参考图（可选）']]){
 const box=document.createElement('div'),l=document.createElement('label'),input=document.createElement('input'),name=document.createElement('p'),remove=document.createElement('button');
 l.textContent=label;l.htmlFor=key;input.id=key;input.type='file';input.accept='image/png,image/jpeg,image/webp';name.id=key+'-state';name.className='file-state';remove.type='button';remove.className='secondary';remove.textContent='移除';
 remove.onclick=()=>{state.settings[key]=[];input.value='';renderFiles();changed()};
 input.onchange=async()=>{const file=input.files[0];if(!file)return;if(file.size>10*1024*1024){status('图片不能超过10MB。',true);input.value='';return}busy=true;controls();status('正在上传图片…');try{const result=await bridge.upload('reference/'+key,file);state.settings[key]=[result.path];renderFiles();changed();status('图片已上传，点击“保存修改”后生效。')}catch(e){status('上传失败：'+e.message,true)}finally{input.value='';busy=false;controls()}};
 box.append(l,name,input,remove);$('references').append(box);
}
function renderFiles(){for(const key of ['chibi_reference','turnaround_reference'])$(key+'-state').textContent=state.settings[key]?.length?'已选择：'+state.settings[key][0].split('/').pop():'尚未选择图片'}
function render(){for(const key of ['character_prompt','review_prompt',...Object.keys(labels)])$(key).value=state.settings[key]||'';renderFiles();counts()}
async function load(){busy=true;controls();status('正在载入…');try{state=await bridge.apiGet('customization');render();dirty=false;loaded=true;status('已载入，修改后请保存。')}catch(e){status('载入失败：'+e.message,true)}finally{busy=false;controls()}}
$('editor').addEventListener('input',e=>{if(e.target.tagName==='TEXTAREA')changed()});
$('defaults').onclick=()=>{if(!confirm('将固定回复重置为通用文案？保存后生效，提示词和参考图不会改变。'))return;for(const key of Object.keys(labels))$(key).value=state.defaults[key];changed()};
$('reload').onclick=()=>{if(!dirty||confirm('重新载入会丢弃尚未保存的修改，是否继续？'))load()};
$('editor').onsubmit=async e=>{e.preventDefault();if(busy||!loaded)return;const settings={...state.settings};for(const key of ['character_prompt','review_prompt',...Object.keys(labels)])settings[key]=$(key).value;busy=true;controls();status('正在保存…');try{state=await bridge.apiPost('customization/save',{revision:state.revision,settings});render();dirty=false;status('已保存，对后续投喂生效。')}catch(e){status('保存失败：'+e.message,true)}finally{busy=false;controls()}};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
try{if(!bridge)throw new Error('请从 AstrBot 插件页面打开此页面');const context=await bridge.ready();document.body.classList.toggle('dark',!!context.isDark);bridge.onContext(c=>document.body.classList.toggle('dark',!!c.isDark));await load()}catch(e){status(e.message,true)}

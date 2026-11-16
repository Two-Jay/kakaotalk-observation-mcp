let state = null, activeTab = 'checks', toastTimer;
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = stamp => stamp ? new Date(typeof stamp === 'number' ? stamp * 1000 : stamp).toLocaleString('ko-KR') : '아직 점검하지 않음';
const empty = (title, text) => `<div class="empty"><strong>${escape(title)}</strong>${escape(text)}</div>`;
const labels = {ok:'완료',error:'실패',queued:'대기',running:'점검 중',cancelled:'취소'};
const badge = status => `<span class="badge ${escape(status)}">${escape(labels[status] || status)}</span>`;
function toast(message, error = false) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className='show'+(error?' error':''); toastTimer=setTimeout(()=>$('toast').className='',6500); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Monitor-Token':state.token}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '요청에 실패했습니다.');
  return data;
}
async function refresh() {
  try { state = await api('/api/state'); render(); }
  catch(error) { toast('서버에 연결할 수 없습니다. '+error.message,true); }
}
function render() {
  $('policy-count').textContent=state.policies.length;
  $('enabled-count').textContent=state.policies.filter(p=>p.enabled).length;
  $('mention-count').textContent=state.mentions.length;
  $('message-count').textContent=state.history.reduce((sum,r)=>sum+r.messages,0).toLocaleString('ko-KR');
  $('scheduler-status').textContent=state.scheduler_enabled?'켜짐':'꺼짐';
  $('scheduler-dot').className='dot'+(state.scheduler_enabled?'':' gray');
  $('scheduler-toggle').textContent=state.scheduler_enabled?'자동 점검 중지':'자동 점검 시작';
  const policies=state.policies;
  $('policy-list').innerHTML=policies.length?`<div class="table-wrap"><table><thead><tr><th>정책 / 톡방</th><th>키워드</th><th>점검 주기</th><th>자동 점검 대상</th><th>마지막 수집</th><th>관리</th></tr></thead><tbody>${policies.map(p=>`<tr><td><div class="policy-title">${escape(p.name)}</div><small>${escape(p.room)}</small></td><td>${p.keywords.map(k=>`<span class="chip">${escape(k)}</span>`).join('')}</td><td>${p.interval_seconds%60===0?`${p.interval_seconds/60}분`:`${p.interval_seconds}초`}</td><td><span class="badge ${p.enabled?'ok':''}">${p.enabled?'포함':'제외'}</span></td><td>${escape(time(p.collection.last_success_at))}<small>${p.collection.total_messages.toLocaleString('ko-KR')}개 보관</small></td><td><div class="actions"><button class="quiet" data-action="check" data-id="${p.id}">지금 점검</button><button class="quiet" data-action="edit" data-id="${p.id}">수정</button><button class="quiet danger" data-action="delete" data-id="${p.id}">삭제</button></div></td></tr>`).join('')}</tbody></table></div>`:empty('첫 모니터링 정책을 추가하세요','톡방 이름, 찾을 키워드, 점검 주기를 설정하면 준비가 끝납니다.');
  renderActivity();
  $('archive-list').innerHTML=state.history.length?state.history.map(r=>{const p=policies.find(p=>p.room===r.room);return `<div class="archive-row"><div><b>${escape(r.room)}</b><small>${r.messages.toLocaleString('ko-KR')}개 메시지 · 로컬 저장</small></div>${p?`<a class="download" href="/api/transcript/${p.id}">TXT 내려받기 ↓</a>`:'<small>같은 톡방의 정책을 추가하면 내려받을 수 있습니다.</small>'}</div>`;}).join(''):empty('아직 저장된 대화가 없습니다','정책을 추가하고 지금 점검을 실행해 주세요.');
}
function renderActivity() {
  const policyName = id => state.policies.find(p=>p.id===id)?.name || '삭제된 정책';
  if(activeTab==='checks') $('activity-list').innerHTML=state.jobs.length?state.jobs.map(j=>`<div class="activity-row">${badge(j.status)}<div class="body"><b>${escape(policyName(j.policy_id))}</b><p>${escape(j.error || (j.result?`새 메시지 ${j.result.new_messages}개 · 새 감지 ${j.result.new_mentions}개`:(j.status==='queued'?'웹 수집기 실행을 기다리고 있습니다.':j.status==='running'?'카카오톡을 확인하고 있습니다.':'점검이 취소되었습니다.')))}</p></div><time>${escape(time(j.created))}</time></div>`).join(''):empty('아직 점검 기록이 없습니다','수동 또는 자동 점검의 결과가 여기에 표시됩니다.');
  else if(activeTab==='mentions') $('activity-list').innerHTML=state.mentions.length?state.mentions.map(m=>`<div class="activity-row"><span class="badge ok">감지</span><div class="body"><b>${escape(m.room)}</b> ${m.keywords.map(k=>`<span class="chip">${escape(k)}</span>`).join('')}<p>${escape(m.record.message)}</p><small class="muted">${escape(m.record.sender)} · ${escape(m.record.time_label)}</small></div><time>${escape(time(m.at))}</time></div>`).join(''):empty('표시할 감지 기록이 없습니다','새로 수집한 메시지의 본문에 키워드가 있으면 기록합니다.');
  else $('activity-list').innerHTML=state.audit.length?state.audit.map(a=>`<div class="activity-row"><span class="badge">설정</span><div class="body"><b>${escape({'policy.saved':'정책 저장','policy.deleted':'정책 삭제','scheduler.changed':'자동 점검 변경'}[a.action]||a.action)}</b><p>${escape(a.detail)}</p></div><time>${escape(time(a.at))}</time></div>`).join(''):empty('설정 변경 기록이 없습니다','정책과 자동 점검 설정의 변경 이력을 남깁니다.');
}
function editPolicy(policy) {
  $('policy-id').value=policy?.id||''; $('dialog-title').textContent=policy?'정책 수정':'정책 추가';
  $('policy-name').value=policy?.name||''; $('policy-room').value=policy?.room||'';
  $('policy-keywords').value=policy?.keywords.join(', ')||''; $('policy-interval').value=policy?.interval_seconds||300;
  $('policy-enabled').checked=policy?.enabled||false; $('form-error').textContent=''; $('policy-dialog').showModal();
}
$('add-policy').onclick=()=>editPolicy(null);
$('close-dialog').onclick=$('cancel-dialog').onclick=()=>$('policy-dialog').close();
$('refresh').onclick=refresh;
$('policy-form').onsubmit=async event=>{event.preventDefault(); $('save-policy').disabled=true;try {
  const policy={name:$('policy-name').value,room:$('policy-room').value,keywords:$('policy-keywords').value.split(/[,\n]/).map(k=>k.trim()).filter(Boolean),interval_seconds:Number($('policy-interval').value),enabled:$('policy-enabled').checked};
  await api('/api/policies',{id:$('policy-id').value||null,policy}); $('policy-dialog').close();toast('정책을 저장했습니다.');await refresh();
}catch(error){$('form-error').textContent=error.message;}finally{$('save-policy').disabled=false;}};
$('policy-list').onclick=async event=>{const b=event.target.closest('button[data-action]');if(!b)return;const policy=state.policies.find(p=>p.id===b.dataset.id);if(!policy)return;
  if(b.dataset.action==='edit'){editPolicy(policy);return;}
  b.disabled=true;
  try{if(b.dataset.action==='delete'){if(!confirm(`‘${policy.name}’ 정책을 삭제할까요? 기존 대화와 기록은 보존됩니다.`))return;await api('/api/policies/delete',{id:policy.id});toast('정책을 삭제했습니다.');}
  else{if(!$('allow-foreground').checked)throw new Error('위의 카카오톡 화면 조작 안내를 확인하고 체크해 주세요.');await api('/api/check',{id:policy.id,allow_foreground:true});toast('점검을 요청했습니다. 카카오톡 화면을 조작하므로 입력을 잠시 멈춰 주세요.');}await refresh();}catch(error){toast(error.message,true);}finally{b.disabled=false;}};
$('scheduler-toggle').onclick=async()=>{try{const enabled=!state.scheduler_enabled;if(enabled&&!$('allow-foreground').checked)throw new Error('카카오톡 화면 조작 안내를 확인하고 체크해 주세요.');await api('/api/scheduler',{enabled,allow_foreground:$('allow-foreground').checked});toast(enabled?'자동 점검을 시작했습니다. 활성 정책의 주기부터 실행합니다.':'자동 점검을 중지했습니다. 실행 중인 점검은 완료됩니다.');await refresh();}catch(error){toast(error.message,true);}};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{activeTab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(t=>{t.classList.toggle('active',t===b);t.setAttribute('aria-selected',String(t===b));});renderActivity();});
refresh(); setInterval(()=>{if(!$('policy-dialog').open)refresh();},4000);

<script setup lang="ts">
import {computed,nextTick,onMounted,onUnmounted,reactive,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {Plus,Search,Refresh} from '@element-plus/icons-vue'
import {api,stateLabel,validationIssues,type Semester} from '../api'
import {personFormErrors} from '../personValidation'
const route=useRoute(),tab=computed(()=>String(route.params.tab||'semesters'))
const titles:Record<string,string>={semesters:'学期与选课管理',students:'学生信息管理',professors:'教授信息管理',billing:'计费任务中心',audit:'操作审计记录'}
const descriptions:Record<string,string>={semesters:'维护选课开放时间，结束选课并生成最终课表。',students:'维护学生资料与账户，保留历史学习记录。',professors:'维护教授资料、账户与课程授课资格。',billing:'跟踪选课通知和正式账单的可靠送达。',audit:'追溯账户、选课、任课与成绩的重要操作。'}
const terms=ref<Semester[]>([]),people=ref<any[]>([]),courses=ref<any[]>([]),total=ref(0),page=ref(1),query=ref(''),inactive=ref(false),busy=ref(false),loading=ref(false),error=ref(''),events=ref<any[]>([]),counts=ref<Record<string,number>>({}),logs=ref<any[]>([])
const kind=computed(()=>tab.value==='students'?'student':'professor')
const personOpen=ref(false),editing=ref<number|null>(null),secretOpen=ref(false),secret=ref(''),secretUsername=ref(''),secretPersonId=ref<number>(),identityNeedsCorrection=ref(false),personError=ref('')
const form=reactive<any>({username:'',name:'',birth_date:'',identity_number:'',status:'在籍',department:'',graduation_date:null,qualification_ids:[],version:0})
const personAttempted=ref(false),personFormElement=ref<HTMLElement>(),serverPersonIssues=ref<ReturnType<typeof validationIssues>>([])
const actionError=ref('')
const personLabels:Record<string,string>={username:'账号',name:'姓名',birth_date:'出生日期',identity_number:'身份证号码',status:'人员状态',department:'所属院系',graduation_date:'毕业日期',qualification_ids:'任课资格'}
const personIssues=computed(()=>{
  const local=personAttempted.value?personFormErrors(form,kind.value,editing.value!==null).map(item=>({...item,label:personLabels[item.field]||item.field})):[]
  return [...local,...serverPersonIssues.value.filter(item=>!local.some(other=>other.field===item.field))]
})
function fieldError(field:string){return personIssues.value.filter(item=>item.field.split('.')[0]===field).map(item=>item.message).join('；')}
async function focusPersonError(field?:string){
  await nextTick()
  const root=personFormElement.value
  const item=field?Array.from(root?.querySelectorAll<HTMLElement>('[data-field]')||[]).find(element=>element.dataset.field===field.split('.')[0]):root?.querySelector<HTMLElement>('.el-form-item.is-error')
  item?.scrollIntoView({behavior:'smooth',block:'center'})
  item?.querySelector<HTMLInputElement>('input')?.focus({preventScroll:true})
}
watch(()=>({...form,qualification_ids:[...form.qualification_ids]}) as Record<string,any>,(current,previous)=>{
  const changed=(field:string)=>JSON.stringify(current[field])!==JSON.stringify(previous[field])
  serverPersonIssues.value=serverPersonIssues.value.filter(item=>{
    if(item.message.includes('不一致')&&(changed('birth_date')||changed('identity_number')))return false
    return !changed(item.field.split('.')[0])
  })
  personError.value=''
})
const termOpen=ref(false),editingTerm=ref<Semester>(),windowDates=ref<[Date,Date]>()
const termError=ref('')
const summary=ref<any>(null),summaryOpen=ref(false)
let searchTimer:ReturnType<typeof setTimeout>|undefined,timer:ReturnType<typeof setInterval>|undefined
let loadSequence=0
async function load(){const sequence=++loadSequence;loading.value=true;error.value='';try{
  if(tab.value==='semesters'){const result=await api('/semesters');if(sequence===loadSequence)terms.value=result}
  else if(['students','professors'].includes(tab.value)){const result=await api(`/registrar/people/${kind.value}?q=${encodeURIComponent(query.value)}&page=${page.value}&include_inactive=${inactive.value}`);if(sequence!==loadSequence)return;people.value=result.items;total.value=result.total}
  else if(tab.value==='billing'){const result=await api(`/registrar/outbox?page=${page.value}`);if(sequence!==loadSequence)return;events.value=result.items;counts.value=result.counts;total.value=result.total}
  else if(tab.value==='audit'){const result=await api(`/registrar/audit?page=${page.value}`);if(sequence!==loadSequence)return;logs.value=result.items;total.value=result.total}
}catch(e:any){if(sequence===loadSequence)error.value=e.message}finally{if(sequence===loadSequence)loading.value=false}}
watch(tab,()=>{page.value=1;query.value='';actionError.value='';load()});watch([page,inactive],load)
watch(query,()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page.value=1;load()},300)})
async function openPerson(person?:any){
  editing.value=person?.id??null
  identityNeedsCorrection.value=!!person?.identity_needs_correction;personError.value='';personAttempted.value=false;serverPersonIssues.value=[]
  Object.assign(form,{username:person?.username||'',name:person?.name||'',birth_date:person?.birth_date||'',identity_number:'',status:person?.status||(kind.value==='student'?'在籍':'在职'),department:person?.department||'',graduation_date:person?.graduation_date||null,qualification_ids:person?.qualification_ids?[...person.qualification_ids]:[],version:person?.version||0})
  if(kind.value==='professor')try{courses.value=await api('/catalog/courses')}catch(e:any){actionError.value=e.message;return}
  personOpen.value=true
}
async function savePerson(){
  if(busy.value)return
  personAttempted.value=true;personError.value='';serverPersonIssues.value=[]
  if(personIssues.value.length){await focusPersonError();return}
  busy.value=true
  try{const payload={...form,username:form.username.trim(),name:form.name.trim(),status:form.status.trim(),identity_number:form.identity_number.trim().toUpperCase()||null,department:form.department.trim()||null};if(editing.value)delete payload.username
    const result=await api(editing.value?`/registrar/people/${editing.value}`:`/registrar/people/${kind.value}`,editing.value?'PUT':'POST',payload)
    personOpen.value=false;ElMessage.success(editing.value?'资料已更新':'人员已创建');await load()
    if(result.initial_password){secret.value=result.initial_password;secretUsername.value=result.person.username;secretPersonId.value=result.person.id;secretOpen.value=true}
  }catch(e:any){serverPersonIssues.value=validationIssues(e);personError.value=serverPersonIssues.value.length?'':e.message;if(serverPersonIssues.value.length)await focusPersonError()}finally{busy.value=false}
}
async function remove(person:any){try{await ElMessageBox.confirm(`确认停用“${person.name}”？账户会立即退出，开放学期的相关安排会移除，历史成绩与账单保留。`,'确认停用人员',{type:'warning',confirmButtonText:'确认停用',cancelButtonText:'取消'})}catch{return}
  busy.value=true;actionError.value='';try{await api(`/registrar/people/${person.id}`,'DELETE',{version:person.version});ElMessage.success('人员已停用');await load()}catch(e:any){actionError.value=e.message}finally{busy.value=false}}
async function reset(person:any){try{await ElMessageBox.confirm(`为“${person.name}”生成新的初始密码，并撤销其所有登录？`,'重置密码',{confirmButtonText:'重置',cancelButtonText:'取消'})}catch{return}
  busy.value=true;actionError.value='';try{const r=await api(`/registrar/people/${person.id}/reset-password`,'POST');secret.value=r.initial_password;secretUsername.value=person.username;secretPersonId.value=person.id;secretOpen.value=true}catch(e:any){actionError.value=e.message}finally{busy.value=false}}
function editTerm(t:Semester){editingTerm.value=t;windowDates.value=[new Date(t.registration_open),new Date(t.registration_close)];termError.value='';termOpen.value=true}
async function saveTerm(){if(busy.value)return;termError.value='';if(!windowDates.value?.[0]||!windowDates.value?.[1]){termError.value='选课开放时间：请选择完整的开始时间和截止时间后再保存。';return}if(windowDates.value[1]<=windowDates.value[0]){termError.value='选课截止时间必须晚于开始时间，请重新选择时间范围。';return}busy.value=true;actionError.value='';try{await api(`/registrar/semesters/${editingTerm.value!.id}`,'PUT',{version:editingTerm.value!.version,registration_open:windowDates.value[0].toISOString(),registration_close:windowDates.value[1].toISOString()});termOpen.value=false;ElMessage.success('选课时间已更新');await load()}catch(e:any){termError.value=e.message}finally{busy.value=false}}
const closeKeys=new Map<string,string>()
async function closeTerm(t:Semester){try{await ElMessageBox.confirm('系统将取消无授课教授的教学班，按备选补位，再取消不足3人的教学班，并生成最终账单。成功后该学期不能再选课或调整任课。','确认结束选课',{type:'warning',confirmButtonText:'执行结束选课',cancelButtonText:'取消'})}catch{return}
  busy.value=true;actionError.value='';try{const tag=`${t.id}:${t.version}`;if(!closeKeys.has(tag))closeKeys.set(tag,crypto.randomUUID());summary.value=await api(`/registrar/semesters/${t.id}/close`,'POST',{version:t.version},closeKeys.get(tag));summaryOpen.value=true;await load();ElMessage.success('选课已结束，计费任务已进入发送队列')}catch(e:any){actionError.value=e.message}finally{busy.value=false}}
async function retry(id:string){busy.value=true;actionError.value='';try{await api(`/registrar/outbox/${id}/retry`,'POST');ElMessage.success('已安排立即重试');await load()}catch(e:any){actionError.value=e.message}finally{busy.value=false}}
const actionNames:Record<string,string>={LOGIN:'登录',CHANGE_PASSWORD:'修改密码',CREATE_PERSON:'创建人员',UPDATE_PERSON:'修改人员',DEACTIVATE_PERSON:'停用人员',RESET_PASSWORD:'重置密码',SAVE_DRAFT:'保存课表草稿',SUBMIT_SCHEDULE:'提交选课',DELETE_SCHEDULE:'删除课表',UPDATE_TEACHING:'更新任课',UPDATE_GRADES:'录入成绩',CLOSE_REGISTRATION:'结束选课',UPDATE_REGISTRATION_WINDOW:'调整选课时间',RETRY_BILLING:'重试计费'}
onMounted(()=>{load();timer=setInterval(()=>{if(tab.value==='billing'&&!busy.value&&!loading.value&&!document.hidden)load()},5000)})
onUnmounted(()=>{clearTimeout(searchTimer);clearInterval(timer)})
</script>
<template><div><div class="page-heading"><div><div class="eyebrow">REGISTRAR WORKSPACE</div><h1>{{titles[tab]||'教务管理'}}</h1><p class="muted">{{descriptions[tab]}}</p></div><el-button :icon="Refresh" @click="load" :disabled="busy||loading">刷新</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" class="mb" show-icon/>
  <el-alert v-if="actionError" :title="actionError" type="error" show-icon class="mb" @close="actionError=''"/>
  <div v-if="tab==='semesters'" v-loading="loading"><div class="notice mb">课程目录由外部系统维护，本系统仅提供只读查询。结束选课将冻结正式课表，计费服务暂不可用时会持续重试。</div><section v-for="t in terms" :key="t.id" class="panel mb"><div class="panel-heading"><div><h2>{{t.name}}</h2><p class="muted">{{t.start_date}} 至 {{t.end_date}}</p></div><el-tag :type="t.state==='OPEN'?'success':'info'">{{stateLabel(t.state)}}</el-tag></div><div class="semester-details"><div><span>选课开始</span><b>{{new Date(t.registration_open).toLocaleString('zh-CN')}}</b></div><div><span>选课截止</span><b>{{new Date(t.registration_close).toLocaleString('zh-CN')}}</b></div><div><span>当前状态</span><b>{{t.can_register?'正在接受选课':t.state==='OPEN'?'不在开放时间内':'已锁定选课与任课'}}</b></div></div><div class="panel-actions"><template v-if="t.state==='OPEN'"><el-button :disabled="busy" @click="editTerm(t)">调整开放时间</el-button><el-button type="danger" :loading="busy" @click="closeTerm(t)">结束选课</el-button></template><el-button v-if="t.close_summary" @click="summary=t.close_summary;summaryOpen=true">查看结束结果</el-button></div></section></div>
  <section v-if="['students','professors'].includes(tab)" class="panel" v-loading="loading"><div class="panel-heading"><h2>{{tab==='students'?'学生':'教授'}}列表 <small>{{total}} 人</small></h2><el-button type="primary" :icon="Plus" :disabled="busy" @click="openPerson()">{{tab==='students'?'添加学生':'添加教授'}}</el-button></div><div class="filter-row"><el-input v-model="query" :prefix-icon="Search" aria-label="搜索人员" placeholder="按姓名、账号或编号搜索" clearable/><el-checkbox v-model="inactive">显示已停用人员</el-checkbox></div><el-table :data="people" empty-text="未找到人员"><el-table-column prop="id" label="编号" width="80"/><el-table-column prop="name" label="姓名" min-width="100"/><el-table-column prop="username" label="账号" min-width="120"/><el-table-column prop="identity_masked" label="身份号码" min-width="140"/><el-table-column :prop="tab==='professors'?'department':'graduation_date'" :label="tab==='professors'?'所属院系':'毕业日期'" min-width="120"/><el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.active?'success':'info'">{{row.active?row.status:'已停用'}}</el-tag></template></el-table-column><el-table-column label="操作" width="240"><template #default="{row}"><el-button text type="primary" :disabled="busy||!row.active" @click="openPerson(row)">编辑</el-button><el-button text :disabled="busy||!row.active" @click="reset(row)">重置密码</el-button><el-button text type="danger" :disabled="busy||!row.active" @click="remove(row)">停用</el-button></template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total,prev,pager,next"/></section>
  <div v-if="tab==='billing'"><div class="stat-grid"><div class="stat-card"><span>已送达</span><strong class="text-green">{{counts.SENT||0}}</strong><small>计费服务已确认接收</small></div><div class="stat-card"><span>等待发送或重试</span><strong>{{counts.PENDING||0}}</strong><small>恢复连接后自动继续</small></div><div class="stat-card"><span>正在发送</span><strong>{{counts.PROCESSING||0}}</strong><small>中断任务可自动恢复</small></div></div><section class="panel" v-loading="loading"><div class="panel-heading"><h2>发送记录</h2><span class="subtle">每5秒自动刷新</span></div><el-table :data="events" empty-text="暂无计费任务"><el-table-column label="通知类型" width="150"><template #default="{row}">{{row.kind==='InvoiceFinalized'?'正式账单':'选课变更通知'}}</template></el-table-column><el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="row.state==='SENT'?'success':'warning'">{{stateLabel(row.state)}}</el-tag></template></el-table-column><el-table-column prop="attempts" label="尝试次数" width="100"/><el-table-column prop="last_error" label="最近结果" min-width="230"><template #default="{row}">{{row.last_error||(row.state==='SENT'?'已确认送达':'等待工作进程处理')}}</template></el-table-column><el-table-column label="创建时间" min-width="170"><template #default="{row}">{{new Date(row.created_at).toLocaleString('zh-CN')}}</template></el-table-column><el-table-column label="操作" width="110"><template #default="{row}"><el-button v-if="row.state==='PENDING'" text type="primary" :disabled="busy" @click="retry(row.id)">立即重试</el-button></template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total,prev,pager,next"/></section></div>
  <section v-if="tab==='audit'" class="panel" v-loading="loading"><div class="panel-heading"><h2>操作记录</h2><span class="subtle">记录关键操作，不记录密码及完整身份号码</span></div><el-table :data="logs"><el-table-column prop="id" label="记录编号" width="110"/><el-table-column prop="actor_id" label="操作人编号" width="120"/><el-table-column label="操作"><template #default="{row}">{{actionNames[row.action]||row.action}}</template></el-table-column><el-table-column prop="target" label="操作对象"/><el-table-column label="时间"><template #default="{row}">{{new Date(row.created_at).toLocaleString('zh-CN')}}</template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total,prev,pager,next"/></section>
  <el-dialog v-model="personOpen" :title="`${editing?'编辑':'添加'}${kind==='student'?'学生':'教授'}`" width="660px" :close-on-click-modal="false">
    <div ref="personFormElement" class="person-form">
      <div v-if="personIssues.length" class="form-error-summary mb" role="alert" aria-label="资料校验结果">
        <strong>有 {{personIssues.length}} 项资料需要修改，请按下列提示检查：</strong>
        <ul><li v-for="(issue,index) in personIssues" :key="`${issue.field}-${index}`"><button type="button" @click="focusPersonError(issue.field)">{{issue.label}}：{{issue.message}}</button></li></ul>
        <p>点击提示可定位到对应输入框；已填写的内容会保留，修改后再保存。</p>
      </div>
      <el-alert v-if="personError" :title="personError" type="error" :closable="false" show-icon class="mb"/>
      <el-alert v-if="identityNeedsCorrection" title="该历史记录的身份证信息需要核对，请填写正确的18位身份证号码。留空仍保留原记录。" type="warning" :closable="false" class="mb"/>
      <p v-if="editing" class="subtle">系统编号：{{editing}}</p>
      <el-form label-position="top" :disabled="busy" @submit.prevent="savePerson">
        <div class="form-grid">
          <el-form-item v-if="!editing" label="账号 *" data-field="username" :error="fieldError('username')">
            <el-input v-model="form.username" aria-label="新人员账号" :aria-invalid="!!fieldError('username')" placeholder="3～64位字母、数字、点、下划线或短横线" maxlength="64"/>
            <span class="field-hint">可使用学号作为账号；不能包含汉字、空格或其他符号。</span>
          </el-form-item>
          <el-form-item label="姓名 *" data-field="name" :error="fieldError('name')"><el-input v-model="form.name" aria-label="姓名" :aria-invalid="!!fieldError('name')" maxlength="80" placeholder="请填写姓名，最多80个字符"/></el-form-item>
          <el-form-item label="出生日期 *" data-field="birth_date" :error="fieldError('birth_date')"><el-date-picker v-model="form.birth_date" aria-label="出生日期" :aria-invalid="!!fieldError('birth_date')" value-format="YYYY-MM-DD" type="date" placeholder="如 2004-01-01"/><span class="field-hint">必须早于今天，并与身份证上的出生日期一致。</span></el-form-item>
          <el-form-item :label="editing?'身份号码（留空不修改）':'身份号码 *'" data-field="identity_number" :error="fieldError('identity_number')"><el-input v-model="form.identity_number" aria-label="身份号码" :aria-invalid="!!fieldError('identity_number')" maxlength="18" placeholder="18位身份证号码，末位可以是X"/><span class="field-hint">前17位为数字，最后一位为数字或X；日期和校验码都需正确。</span></el-form-item>
          <el-form-item label="状态 *" data-field="status" :error="fieldError('status')"><el-input v-model="form.status" aria-label="人员状态" :aria-invalid="!!fieldError('status')" maxlength="30" placeholder="如在籍、休学；最多30个字符"/></el-form-item>
          <el-form-item v-if="kind==='student'" label="毕业日期" data-field="graduation_date" :error="fieldError('graduation_date')"><el-date-picker v-model="form.graduation_date" aria-label="毕业日期" :aria-invalid="!!fieldError('graduation_date')" value-format="YYYY-MM-DD" type="date" placeholder="可留空，填写时须晚于出生日期"/></el-form-item>
          <el-form-item v-else label="所属院系 *" data-field="department" :error="fieldError('department')"><el-input v-model="form.department" aria-label="所属院系" :aria-invalid="!!fieldError('department')" maxlength="80" placeholder="请填写所属院系，最多80个字符"/></el-form-item>
        </div>
        <el-form-item v-if="kind==='professor'" label="具备授课资格的课程" data-field="qualification_ids" :error="fieldError('qualification_ids')"><el-select v-model="form.qualification_ids" aria-label="任课资格" :aria-invalid="!!fieldError('qualification_ids')" multiple filterable placeholder="选择课程"><el-option v-for="c in courses" :key="c.id" :value="c.id" :label="`${c.code} ${c.name}`"/></el-select></el-form-item>
        <p class="subtle" v-if="!editing">创建后会生成一次性初始密码，首次登录必须修改。</p>
      </el-form>
    </div>
    <template #footer><el-button @click="personOpen=false">取消</el-button><el-button type="primary" :loading="busy" @click="savePerson">保存资料</el-button></template>
  </el-dialog>
  <el-dialog v-model="secretOpen" title="请妥善交付初始密码" width="470px" @closed="secret=''"><p>系统编号：<b>{{secretPersonId}}</b></p><p>账号：<b>{{secretUsername}}</b></p><div class="generated-password">{{secret}}</div><p class="muted">此密码仅在这里显示一次。用户首次登录需要修改密码。</p><template #footer><el-button type="primary" @click="secretOpen=false">已记录</el-button></template></el-dialog>
  <el-dialog v-model="termOpen" title="调整选课开放时间" width="620px"><el-alert v-if="termError" :title="termError" type="error" :closable="false" show-icon class="mb"/><el-date-picker v-model="windowDates" type="datetimerange" range-separator="至" start-placeholder="开放时间" end-placeholder="截止时间" style="width:100%"/><template #footer><el-button @click="termOpen=false">取消</el-button><el-button type="primary" :loading="busy" @click="saveTerm">保存时间</el-button></template></el-dialog>
  <el-dialog v-model="summaryOpen" title="结束选课处理结果" width="700px"><template v-if="summary"><div class="stat-grid"><div class="stat-card"><span>确认开班</span><strong>{{summary.confirmed_offerings}}</strong></div><div class="stat-card"><span>备选补位</span><strong>{{summary.promoted.length}}</strong></div><div class="stat-card"><span>生成账单</span><strong>{{summary.invoices}}</strong></div></div><h3>取消的教学班</h3><el-table :data="summary.cancelled" empty-text="没有教学班被取消"><el-table-column prop="code" label="教学班"/><el-table-column prop="reason" label="取消原因"/></el-table></template><template #footer><el-button type="primary" @click="summaryOpen=false">完成</el-button></template></el-dialog>
</div></template>

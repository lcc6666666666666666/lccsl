<script setup lang="ts">
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {Search,Refresh,Check,Document,Delete} from '@element-plus/icons-vue'
import {api,allOfferings,meetingText,stateLabel,type Semester,type Offering,type Schedule} from '../api'
import {useAuth} from '../store'
import Timetable from '../components/Timetable.vue'
const auth=useAuth(),terms=ref<Semester[]>([]),termId=ref<number>(),offers=ref<Offering[]>([])
const schedule=ref<Schedule>({version:0,exists:false,ever_submitted:false,primary:[],alternates:[],draft:null})
const primary=ref<number[]>([]),alternates=ref<number[]>([]),tab=ref('courses'),query=ref(''),page=ref(1),busy=ref(false),loading=ref(false),error=ref(''),report=ref<any>(null),bills=ref<any[]>([])
const current=computed(()=>terms.value.find(t=>t.id===termId.value))
const selected=computed(()=>offers.value.filter(o=>primary.value.includes(o.id)))
const official=computed(()=>offers.value.filter(o=>schedule.value.primary.includes(o.id)))
const filtered=computed(()=>offers.value.filter(o=>`${o.name} ${o.course_code} ${o.professor_name||''}`.toLowerCase().includes(query.value.toLowerCase())))
const visible=computed(()=>filtered.value.slice((page.value-1)*6,page.value*6))
const dirty=computed(()=>JSON.stringify({primary:primary.value,alternates:alternates.value})!==JSON.stringify(schedule.value.draft??{primary:schedule.value.primary,alternates:schedule.value.alternates}))
watch(query,()=>page.value=1)
function applySchedule(value:Schedule){schedule.value=value;primary.value=[...(value.draft?.primary??value.primary)];alternates.value=[...(value.draft?.alternates??value.alternates)]}
let loadSequence=0
async function load(){
  if(!termId.value)return
  const sequence=++loadSequence,id=termId.value
  loading.value=true;error.value=''
  try{const [catalog,s,newTerms]=await Promise.all([allOfferings(id),api<Schedule>(`/student/schedules/${id}`),api<Semester[]>('/semesters')]);if(sequence===loadSequence){offers.value=catalog;applySchedule(s);terms.value=newTerms}}catch(e:any){if(sequence===loadSequence)error.value=e.message}finally{if(sequence===loadSequence)loading.value=false}
}
watch(termId,()=>{page.value=1;offers.value=[];applySchedule({version:0,exists:false,ever_submitted:false,primary:[],alternates:[],draft:null});load()})
function select(o:Offering,kind:'primary'|'alternate'){
  const list=kind==='primary'?primary:alternates,other=kind==='primary'?alternates:primary
  if(list.value.includes(o.id)){list.value=list.value.filter(id=>id!==o.id);return}
  if(list.value.length>=(kind==='primary'?4:2)){ElMessage.warning(kind==='primary'?'首选最多4门':'备选最多2门');return}
  const existing=offers.value.find(v=>[...primary.value,...alternates.value].includes(v.id)&&v.course_id===o.course_id&&v.id!==o.id)
  if(existing){ElMessage.warning('同一课程不能选择多个教学班');return}
  other.value=other.value.filter(id=>id!==o.id);list.value.push(o.id)
}
let submitKey:string|undefined,submitBody=''
async function save(submit=false){
  if(!termId.value)return
  busy.value=true
  try{
    const payload={version:schedule.value.version,primary:primary.value,alternates:alternates.value}
    if(submit){
      if(JSON.stringify(payload)!==submitBody){submitKey=crypto.randomUUID();submitBody=JSON.stringify(payload)}
      const latest=await allOfferings(termId.value)
      offers.value=latest
      const full=latest.find(o=>primary.value.includes(o.id)&&!schedule.value.primary.includes(o.id)&&o.remaining===0)
      if(full){ElMessage.warning(`${full.name}已满员，请调整首选后再提交`);return}
    }
    const result=await api<Schedule>(`/student/schedules/${termId.value}/${submit?'submit':'draft'}`,submit?'POST':'PUT',payload,submit?submitKey:undefined)
    applySchedule(result);submitKey=undefined;submitBody='';ElMessage.success(submit?'选课提交成功，正式课表已更新':'草稿已保存，正式选课和名额未改变');offers.value=await allOfferings(termId.value)
  }catch(e:any){ElMessage.error(e.message)}finally{busy.value=false}
}
async function remove(){
  try{await ElMessageBox.confirm('删除后将退出本学期所有已注册的教学班，并清除草稿。','确认删除课表',{type:'warning',confirmButtonText:'确认删除',cancelButtonText:'取消'})}catch{return}
  busy.value=true
  try{applySchedule(await api(`/student/schedules/${termId.value}`,'DELETE',{version:schedule.value.version}));offers.value=await allOfferings(termId.value!);ElMessage.success('课表已删除')}catch(e:any){ElMessage.error(e.message)}finally{busy.value=false}
}
async function showReport(id?:number){try{report.value=await api(`/student/report-card${id?`?semester_id=${id}`:''}`)}catch(e:any){report.value=null;ElMessage.info(e.message)}}
watch(tab,async value=>{if(value==='grades')await showReport();if(value==='bills')try{bills.value=await api('/student/invoices')}catch(e:any){ElMessage.error(e.message)}})
let timer:ReturnType<typeof setInterval>|undefined,polling=false
onMounted(async()=>{
  try{terms.value=await api('/semesters');termId.value=terms.value.find(t=>t.can_register)?.id??terms.value[0]?.id}catch(e:any){error.value=e.message}
  timer=setInterval(async()=>{if(!termId.value||polling||busy.value||loading.value||document.hidden||tab.value!=='courses')return;polling=true
    const id=termId.value
    try{const [rows,newTerms]=await Promise.all([allOfferings(id),api<Semester[]>('/semesters')]);if(id!==termId.value)return;for(const o of rows){const before=offers.value.find(x=>x.id===o.id);if(before&&before.remaining>0&&o.remaining===0&&primary.value.includes(o.id)&&!schedule.value.primary.includes(o.id))ElMessage.warning(`${o.name}刚刚满员，请调整首选`)}offers.value=rows;terms.value=newTerms;error.value=''}catch(e:any){if(id===termId.value)error.value=e.message}finally{polling=false}},5000)
})
onUnmounted(()=>clearInterval(timer))
</script>
<template><div>
  <div class="page-heading"><div><div class="eyebrow">STUDENT WORKSPACE</div><h1>{{auth.user?.name}}，开始你的学习计划</h1><p class="muted">了解课程，安排时间，选择适合自己的方向。</p></div><el-select v-model="termId" :disabled="busy" aria-label="选择学期" class="term-select"><el-option v-for="t in terms" :key="t.id" :label="t.name" :value="t.id"/></el-select></div>
  <div class="stat-grid"><div class="stat-card"><span>当前学期状态</span><strong :class="current?.can_register?'text-green':''">{{current?stateLabel(current.state):'加载中'}}</strong><small v-if="current">截止 {{new Date(current.registration_close).toLocaleString('zh-CN')}}</small></div><div class="stat-card"><span>正式注册</span><strong>{{schedule.primary.length}} <em>/ 4 门</em></strong><small>成功提交后计入正式课表</small></div><div class="stat-card"><span>可选教学班</span><strong>{{offers.filter(o=>o.state==='OPEN'&&o.remaining>0).length}} <em>个</em></strong><small>每班最多 10 人，余量自动刷新</small></div></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb"/>
  <el-tabs v-model="tab" class="workspace-tabs"><el-tab-pane label="课程选择" name="courses"/><el-tab-pane label="我的课表" name="schedule"/><el-tab-pane label="学期成绩单" name="grades"/><el-tab-pane label="费用账单" name="bills"/></el-tabs>
  <div v-if="tab==='courses'" class="selection-layout" v-loading="loading">
    <section class="panel"><div class="panel-heading"><h2>课程目录 <small>{{filtered.length}} 个教学班</small></h2><div class="toolbar"><el-input v-model="query" :prefix-icon="Search" placeholder="搜索课程、课程代码或教授" aria-label="搜索课程" clearable/><el-button :icon="Refresh" aria-label="刷新课程" @click="load" :disabled="busy||dirty"/></div></div>
      <div v-if="!current?.can_register" class="notice">当前学期不在选课开放期间，可查看已有课表与课程信息。</div>
      <div class="course-list"><article v-for="o in visible" :key="o.id" class="course-card"><div class="course-top"><span class="course-code">{{o.course_code}} <span>· {{o.code}}</span></span><el-tag :type="o.state!=='OPEN'?'info':o.remaining===0?'danger':'success'" effect="plain">{{o.state!=='OPEN'?stateLabel(o.state):o.remaining===0?'已满员':`剩余 ${o.remaining} 名额`}}</el-tag></div><h3>{{o.name}}</h3><p class="course-meta">{{o.professor_name||'待安排授课教授'}} · {{o.department}} · {{o.credits}} 学分</p><p class="course-time">{{meetingText(o)}}</p><p class="prereq" :class="{'text-amber':!o.prerequisites_met}">先修要求：{{o.prerequisites.map(p=>p.name).join('、')||'无'}}<span v-if="o.prerequisites.length"> · {{o.prerequisites_met?'已满足':'未满足'}}</span></p><div class="course-bottom"><span>¥{{o.fee}} <small>/ 学期</small></span><div><el-button size="small" :type="alternates.includes(o.id)?'warning':'default'" :disabled="busy||!current?.can_register||o.state!=='OPEN'" @click="select(o,'alternate')">{{alternates.includes(o.id)?`备选 ${alternates.indexOf(o.id)+1} · 移除`:'加入备选'}}</el-button><el-button size="small" :type="primary.includes(o.id)?'success':'primary'" :plain="!primary.includes(o.id)" :disabled="busy||!current?.can_register||o.state!=='OPEN'||(!primary.includes(o.id)&&(!o.prerequisites_met||(o.remaining===0&&!schedule.primary.includes(o.id))))" @click="select(o,'primary')">{{primary.includes(o.id)?'已选首选 · 移除':'加入首选'}}</el-button></div></div></article><el-empty v-if="!visible.length" description="未找到符合条件的课程"/></div>
      <el-pagination v-model:current-page="page" :page-size="6" :total="filtered.length" layout="prev, pager, next" hide-on-single-page/>
    </section>
    <aside class="panel selection-panel"><div class="panel-heading"><h2>我的选择</h2><el-tag size="small" :type="dirty?'warning':'info'">{{dirty?'未保存':schedule.draft?'已保存草稿':'与正式课表同步'}}</el-tag></div><p class="muted selection-note">{{schedule.ever_submitted?'可调整为1～4门首选、0～2门备选。':'首次提交需选满4门首选和2门备选。'}}备选不提前占用名额。</p><h4>首选课程 <span>{{primary.length}} / 4</span></h4><div v-for="(id,i) in primary" :key="id" class="selected-row"><span class="number-chip">{{i+1}}</span><b>{{offers.find(o=>o.id===id)?.name||id}}</b><el-button text :disabled="busy||!current?.can_register" @click="primary=primary.filter(x=>x!==id)">移除</el-button></div><div v-if="!primary.length" class="empty-selection">从左侧课程目录加入首选</div><h4>备选课程 <span>{{alternates.length}} / 2</span></h4><div v-for="(id,i) in alternates" :key="id" class="selected-row"><span class="number-chip alternate">{{i+1}}</span><b>{{offers.find(o=>o.id===id)?.name||id}}</b><el-button text :disabled="busy||!current?.can_register" @click="alternates=alternates.filter(x=>x!==id)">移除</el-button></div><div v-if="!alternates.length" class="empty-selection">按优先顺序选择备选课程</div><div class="selection-actions"><el-button :icon="Document" :disabled="!current?.can_register||busy" @click="save(false)">保存草稿</el-button><el-button type="primary" :icon="Check" :loading="busy" :disabled="!current?.can_register" @click="save(true)">提交选课</el-button></div><p class="subtle">保存草稿不会变更已注册课程。提交时会检查名额、先修条件和上课时间。</p></aside>
  </div>
  <section v-if="tab==='schedule'" class="panel"><div class="panel-heading"><h2>正式课表 <small>仅展示已成功注册的课程</small></h2><el-button type="danger" plain :icon="Delete" :disabled="busy||!schedule.exists||!current?.can_register" @click="remove">删除课表</el-button></div><Timetable v-if="official.length" :offerings="official"/><el-empty v-else description="还没有正式注册的课程"/><div v-if="schedule.draft" class="notice">你有已保存的草稿。草稿中的调整尚未生效，请在“课程选择”中提交。</div></section>
  <section v-if="tab==='grades'" class="panel"><div class="panel-heading"><h2>学期成绩单</h2><el-select :model-value="report?.semester.id" aria-label="成绩学期" @change="showReport"><el-option v-for="t in terms.filter(t=>t.completed)" :key="t.id" :label="t.name" :value="t.id"/></el-select></div><el-table :data="report?.items||[]" empty-text="该学期暂无成绩信息"><el-table-column prop="code" label="课程代码"/><el-table-column prop="course" label="课程名称"/><el-table-column prop="credits" label="学分"/><el-table-column label="成绩"><template #default="{row}"><el-tag :type="['F','I'].includes(row.grade)?'warning':'success'">{{row.grade||'待录入'}}</el-tag></template></el-table-column></el-table></section>
  <section v-if="tab==='bills'" class="panel"><div class="panel-heading"><h2>最终费用账单</h2></div><el-empty v-if="!bills.length" description="选课结束后将生成最终账单"/><div v-for="bill in bills" :key="bill.id" class="invoice"><h3>{{terms.find(t=>t.id===bill.semester_id)?.name}} <span>合计 ¥{{bill.total}}</span></h3><el-table :data="bill.lines"><el-table-column prop="code" label="教学班"/><el-table-column prop="course" label="课程"/><el-table-column prop="fee" label="费用（元）"/></el-table></div></section>
</div></template>

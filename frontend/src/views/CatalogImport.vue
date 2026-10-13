<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {Download,UploadFilled,Refresh,DocumentChecked} from '@element-plus/icons-vue'
import {api,ApiError,type Semester} from '../api'

interface ImportMeeting {weekday:number;start:string;end:string;week_start:number;week_end:number;room:string}
interface ImportResult {filename:string;semester_name:string;sha256:string;applied:boolean;new_courses:number;existing_courses:number;new_offerings:number;existing_offerings:number;new_prerequisites:number;new_meetings:number;items:{code:string;course_code:string;name:string;fee:string;action:string;meetings:ImportMeeting[]}[]}
interface FileIssue {field:string;location:string;message:string}
const terms=ref<Semester[]>([]),semesterId=ref<number>(),source=ref<any>(null)
const filename=ref(''),content=ref(''),fileSize=ref(0),fileInput=ref<HTMLInputElement>()
const busy=ref(''),loading=ref(false),error=ref(''),issues=ref<FileIssue[]>([]),result=ref<ImportResult>()
const selectedTerm=computed(()=>terms.value.find(t=>t.id===semesterId.value))
const canImport=computed(()=>!!filename.value&&!!content.value&&selectedTerm.value?.state==='OPEN'&&!busy.value&&!loading.value)
const resultPage=ref(1)
const resultRows=computed(()=>result.value?.items.slice((resultPage.value-1)*20,resultPage.value*20)||[])
function clearResult(){result.value=undefined;resultPage.value=1;error.value='';issues.value=[]}
watch(semesterId,clearResult)
function showError(e:unknown){
  if(e instanceof ApiError&&e.code==='CATALOG_IMPORT_INVALID'){
    issues.value=Array.isArray(e.details)?e.details:[]
    error.value=`文件校验未通过，共显示${issues.value.length}处问题，本次没有导入任何数据。请修改后重新选择文件。`
  }else error.value=e instanceof Error?e.message:'操作失败，请稍后重试'
}
async function refresh(){
  loading.value=true
  try{
    const [list,status]=await Promise.all([api<Semester[]>('/semesters'),api('/registrar/catalog/source')])
    terms.value=list;source.value=status
    if(!semesterId.value)semesterId.value=list.find(t=>t.state==='OPEN')?.id
  }catch(e){showError(e)}finally{loading.value=false}
}
async function chooseFile(event:Event){
  const file=(event.target as HTMLInputElement).files?.[0]
  clearResult();filename.value='';content.value='';fileSize.value=0
  if(!file)return
  if(!file.name.toLowerCase().endsWith('.json')){error.value='请选择.json课程目录文件。可以先下载当前学期的示例文件。';return}
  if(file.size>256*1024){error.value='文件超过256KB，请拆分成多个文件导入。';return}
  if(!file.size){error.value='文件内容为空，请填写课程目录后重试。';return}
  busy.value='reading'
  try{
    const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer())
    content.value=text;filename.value=file.name;fileSize.value=file.size
  }catch{error.value='文件读取失败或不是有效UTF-8编码，请另存为UTF-8编码的JSON文件后重试。'}
  finally{busy.value='';if(fileInput.value)fileInput.value.value=''}
}
async function downloadExample(){
  if(!semesterId.value||busy.value)return
  busy.value='download';error.value=''
  try{
    const document=await api(`/registrar/catalog/template?semester_id=${semesterId.value}`)
    const url=URL.createObjectURL(new Blob([JSON.stringify(document,null,2)+'\n'],{type:'application/json;charset=utf-8'}))
    const link=window.document.createElement('a');link.href=url;link.download=`课程目录示例_${selectedTerm.value?.code||'学期'}.json`
    window.document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }catch(e){showError(e)}finally{busy.value=''}
}
async function submit(preview:boolean){
  if(!canImport.value)return
  clearResult();busy.value=preview?'preview':'import'
  try{
    result.value=await api<ImportResult>(`/registrar/catalog/${preview?'preview':'import'}`,'POST',{
      semester_id:semesterId.value,filename:filename.value,content:content.value,
    })
    if(!preview)await refresh()
  }catch(e){showError(e)}finally{busy.value=''}
}
function period(m:ImportMeeting){return `周${'一二三四五六日'[m.weekday-1]} ${m.start}–${m.end} · 第${m.week_start}–${m.week_end}周 · ${m.room}`}
onMounted(refresh)
</script>

<template><div class="catalog-import">
  <div class="page-heading"><div><p class="eyebrow">EXTERNAL COURSE CATALOG</p><h1>课程文件导入</h1><p class="muted">将课程目录文件作为外部数据源，一次导入课程、教学班、先修关系和上课时间。</p></div><el-button :icon="Refresh" :disabled="!!busy" :loading="loading" @click="refresh">刷新状态</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb"/>
  <section v-if="issues.length" class="panel mb" aria-label="文件错误明细"><div class="panel-heading"><h2>请修改以下位置 <small>整批未保存</small></h2></div><el-table :data="issues" stripe max-height="460"><el-table-column prop="location" label="文件位置" min-width="210"/><el-table-column prop="message" label="原因与修改方法" min-width="360"/></el-table></section>
  <section class="source-card mb" v-if="source"><el-icon><DocumentChecked/></el-icon><div><strong>{{source.mode==='file'?'文件目录已启用':'尚未导入文件目录'}}</strong><p>{{source.message}}</p><p v-if="source.filename">最近导入：{{source.filename}} · {{new Date(source.imported_at).toLocaleString()}} · 累计 {{source.course_count}} 门课程 / {{source.offering_count}} 个教学班</p></div><el-tag :type="source.mode==='file'?'success':'info'">{{source.mode==='file'?'文件数据源':'外部服务数据源'}}</el-tag></section>
  <section class="panel mb">
    <div class="panel-heading"><h2>导入课程目录</h2><el-tag effect="plain">仅教务管理员</el-tag></div>
    <el-form label-position="top">
      <el-form-item label="目标学期"><el-select v-model="semesterId" aria-label="导入目标学期" :disabled="!!busy||loading" placeholder="请选择开放学期" style="max-width:430px"><el-option v-for="term in terms" :key="term.id" :value="term.id" :label="`${term.name}（${term.code}）`" :disabled="term.state!=='OPEN'"/></el-select><span class="field-hint">文件中的 semester_code 必须与目标学期一致，已结束学期不可导入。</span></el-form-item>
    </el-form>
    <div class="file-picker"><el-icon class="upload-symbol"><UploadFilled/></el-icon><div><strong>{{filename||'选择外部课程目录文件'}}</strong><p>{{filename?`${(fileSize/1024).toFixed(1)} KB · 文件已就绪，可直接一键导入`:'JSON 格式 · UTF-8 编码 · 最大 256 KB'}}</p></div><label class="file-button" :class="{disabled:!!busy||loading}">{{filename?'重新选择文件':'选择 JSON 文件'}}<input ref="fileInput" type="file" accept=".json,application/json" aria-label="课程目录文件" :disabled="!!busy||loading" @change="chooseFile"/></label></div>
    <div class="import-actions"><el-button :icon="Download" :disabled="!semesterId||!!busy||loading" @click="downloadExample">下载当前学期示例</el-button><div><el-button :disabled="!canImport" :loading="busy==='preview'" @click="submit(true)">仅校验预览</el-button><el-button type="primary" :disabled="!canImport" :loading="busy==='import'" :icon="UploadFilled" @click="submit(false)">一键导入</el-button></div></div>
    <el-alert title="新增导入：相同记录自动跳过；已有代码的信息不一致时，整批拒绝并指出具体字段。导入不会删除原课程或改变已有选课、任课和成绩。" type="info" :closable="false" show-icon/>
  </section>
  <section v-if="result" class="panel mb" aria-live="polite">
    <el-alert :title="result.applied?'导入完成，课程目录已保存':'校验通过，以下为预计结果；尚未写入数据库'" :type="result.applied?'success':'info'" :closable="false" show-icon class="mb"/>
    <div class="stat-grid"><div class="stat-card"><span>{{result.applied?'已新增课程':'将新增课程'}}</span><strong>{{result.new_courses}}</strong><small>相同课程跳过 {{result.existing_courses}} 门</small></div><div class="stat-card"><span>{{result.applied?'已新增教学班':'将新增教学班'}}</span><strong>{{result.new_offerings}}</strong><small>相同教学班跳过 {{result.existing_offerings}} 个</small></div><div class="stat-card"><span>关联数据</span><strong>{{result.new_meetings}}</strong><small>段上课时间 / {{result.new_prerequisites}} 条先修关系</small></div></div>
    <p v-if="result.applied" class="followup">新教学班暂未分配教授。请在“教授信息”中维护任课资格，再由教授选择授课；结束选课时，无有效教授的教学班会被取消。</p>
    <div class="panel-heading"><h2>教学班明细 <small>{{result.semester_name}}</small></h2></div>
    <el-table :data="resultRows" stripe><el-table-column prop="code" label="教学班代码" min-width="150"/><el-table-column prop="name" label="课程" min-width="150"/><el-table-column prop="fee" label="费用（元）" width="110"/><el-table-column label="上课安排" min-width="280"><template #default="{row}"><div v-for="(m,i) in row.meetings" :key="i">{{period(m)}}</div></template></el-table-column><el-table-column prop="action" label="处理" width="100"/></el-table>
    <el-pagination v-model:current-page="resultPage" :page-size="20" :total="result.items.length" layout="total,prev,pager,next"/>
  </section>
  <section class="panel import-help"><h2>如何准备文件</h2><ol><li>选择学期，下载示例。用文本编辑器修改 JSON，保留字段名与数据结构。</li><li><code>courses</code>填写课程及先修课程代码；<code>offerings</code>填写教学班、费用和上课时间。同一文件可引用已有课程。</li><li>星期使用 1～7，时间使用 24 小时制，人数上限固定为 10。每次最多 500 门课程、500 个教学班。</li><li>选择文件后点击“一键导入”。全部校验通过才保存；如有错误，页面会保留具体记录、字段和修改建议。</li></ol></section>
</div></template>

<style scoped>
.source-card{display:flex;align-items:center;gap:18px;padding:20px 24px;border:1px solid #dce7f6;border-radius:12px;background:#f0f5ff}.source-card>.el-icon{font-size:30px;color:#3156d8}.source-card>div{flex:1}.source-card p{margin:6px 0 0;color:#687891;font-size:13px;line-height:1.7}.file-picker{display:flex;align-items:center;gap:18px;border:1px dashed #a8b9db;border-radius:12px;padding:28px;background:#fafcff}.upload-symbol{font-size:42px;color:#6385ca}.file-picker>div{flex:1;min-width:0;overflow-wrap:anywhere}.file-picker p{font-size:13px;color:#7b869b;margin:8px 0 0}.file-button{position:relative;border:1px solid #bac8e0;border-radius:7px;background:white;color:#3156d8;font-size:14px;padding:10px 16px;cursor:pointer;overflow:hidden;white-space:nowrap}.file-button:focus-within{outline:2px solid #3156d8;outline-offset:3px}.file-button.disabled{opacity:.5;cursor:not-allowed}.file-button input{position:absolute;inset:0;opacity:0;width:100%;cursor:inherit}.import-actions{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:20px 0}.followup{color:#977029;font-size:13px;line-height:1.8;background:#fff8e9;padding:12px 16px;border-radius:8px}.import-help ol{padding-left:22px;margin:18px 0 0;color:#6c7890;font-size:13px;line-height:2.2}.import-help code{color:#3156d8}.source-card strong{font-size:14px}@media(max-width:650px){.file-picker{flex-wrap:wrap;padding:20px}.source-card{flex-wrap:wrap}.source-card>.el-tag{margin-left:48px}.import-actions>div{display:flex}}
</style>

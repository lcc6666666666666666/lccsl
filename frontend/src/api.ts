export interface User { id:number; username:string; name:string; role:'student'|'professor'|'registrar'; csrf_token:string; must_change_password:boolean }
export interface Semester { id:number;name:string;code:string;state:string;version:number;start_date:string;end_date:string;registration_open:string;registration_close:string;completed:boolean;can_register:boolean;close_summary:any }
export interface Offering { id:number;code:string;course_id:number;course_code:string;name:string;department:string;credits:string;professor_id:number|null;professor_name:string|null;capacity:number;enrolled_count:number;remaining:number;fee:string;state:string;eligible:boolean;prerequisites_met:boolean;cancellation_reason:string|null;prerequisites:{code:string;name:string}[];meetings:{weekday:number;start_minute:number;end_minute:number;week_start:number;week_end:number;room:string}[] }
export interface Schedule { version:number;exists:boolean;ever_submitted:boolean;primary:number[];alternates:number[];draft:{primary:number[];alternates:number[]}|null }
let csrf=''
export function setCsrf(value:string){csrf=value}
export class ApiError extends Error { constructor(public code:string,message:string,public status:number,public details:any,public requestId?:string){super(message);this.name='ApiError'} }
const fieldNames:Record<string,string>={username:'账号',name:'姓名',birth_date:'出生日期',identity_number:'身份证号码',status:'人员状态',department:'所属院系',graduation_date:'毕业日期',qualification_ids:'任课资格',password:'密码',old_password:'当前密码',new_password:'新密码',registration_open:'开放时间',registration_close:'截止时间',primary:'首选课程',alternates:'备选课程',version:'数据版本',grades:'成绩',grade:'成绩',student_id:'学生编号',offering_ids:'任课安排',expected_ids:'原任课安排',q:'搜索内容',page:'页码',page_size:'每页条数',semester_id:'学期编号',offering_id:'教学班编号',course_id:'课程编号',role:'角色',active:'启用状态','X-CSRF-Token':'安全校验信息','Idempotency-Key':'请求标识'}
type ErrorResult={code?:string;message?:string;details?:unknown}
export interface ValidationIssue {field:string;label:string;message:string}
function translatedIssue(raw:string,type:string,field:string,context:Record<string,unknown>):string {
  const message=raw.replace(/^(?:Value error|Assertion failed),\s*/,'').trim()
  if(/[\u4e00-\u9fff]/.test(message))return message
  const stringMinimum=message.match(/^String should have at least ([0-9]+) characters/)
  const stringMaximum=message.match(/^String should have at most ([0-9]+) characters/)
  if(stringMinimum)return `至少需要${stringMinimum[1]}个字符`
  if(stringMaximum)return `最多允许${stringMaximum[1]}个字符`
  const listMinimum=message.match(/^(?:List|Tuple|Set|Frozenset|Dictionary) should have at least ([0-9]+) items?/)
  const listMaximum=message.match(/^(?:List|Tuple|Set|Frozenset|Dictionary) should have at most ([0-9]+) items?/)
  if(listMinimum)return `至少需要${listMinimum[1]}项，请补充后重试`
  if(listMaximum)return `最多允许${listMaximum[1]}项，请减少数量后重试`
  const comparison=message.match(/^Input should be (greater than or equal to|greater than|less than or equal to|less than) (-?[0-9]+(?:\.[0-9]+)?)/)
  if(comparison)return `必须${({'greater than or equal to':'大于或等于','greater than':'大于','less than or equal to':'小于或等于','less than':'小于'} as Record<string,string>)[comparison[1]]}${comparison[2]}`
  if(type==='missing'||message==='Field required')return '此项必填，不能为空'
  if(type==='string_pattern_mismatch'||message.startsWith('String should match pattern'))return field==='username'?'只能使用英文字母、数字、点、下划线或短横线，不能包含中文或空格':'格式不正确，请按照该字段的填写说明修改'
  if(type.startsWith('date')||message.startsWith('Input should be a valid date'))return type.startsWith('datetime')||field.startsWith('registration_')?'请选择有效的日期和时间，选课时间必须包含时区':'请填写有效日期，格式为年-月-日，例如2004-01-01'
  if(type==='int_type'||type==='int_parsing'||type==='int_from_float'||message.startsWith('Input should be a valid integer'))return '必须填写整数，不能使用小数或非数字字符'
  if(type==='float_type'||type==='float_parsing'||type.startsWith('decimal_')||message.startsWith('Input should be a valid number'))return '必须填写有效数字，请检查数字格式和取值范围'
  if(type==='finite_number')return '必须填写有限数值，不能使用无穷大或NaN'
  if(type==='string_type'||message==='Input should be a valid string')return '必须填写文本，不能使用数字、列表或其他类型'
  if(type==='list_type'||message==='Input should be a valid list')return '必须提交列表，请重新选择所需项目'
  if(type==='bool_type'||type==='bool_parsing')return '必须选择是或否，不能填写其他内容'
  if(type==='extra_forbidden'||message==='Extra inputs are not permitted')return '此字段不受支持，请刷新页面后重新填写'
  if(type==='none_required')return '此字段必须留空'
  if(type==='literal_error'||type==='enum'||message.startsWith('Input should be ')){
    if(field==='grade'||field.endsWith('.grade'))return '成绩只能选择A、B、C、D、F、I；暂未录入时请留空'
    const expected=typeof context.expected==='string'?context.expected:message.replace(/^Input should be /,'')
    if(expected&&expected!==message)return `请选择允许的选项：${expected}`
    if(typeof context.expected==='string')return `请选择允许的选项：${context.expected}`
  }
  if(type==='json_invalid'||type==='model_type'||type==='model_attributes_type'||type==='dict_type')return '提交的数据结构无效，请刷新页面重新填写；仍有问题时请联系管理员并提供请求编号'
  return '填写内容无法通过校验，请检查该字段的格式及取值；仍有问题时请联系管理员并提供请求编号'
}

/** Accept both our field/message details and FastAPI/Pydantic loc/msg details. */
export function validationIssues(result:ErrorResult):ValidationIssue[] {
  const details=Array.isArray(result.details)?result.details:result.details&&typeof result.details==='object'?[result.details]:[]
  const issues:ValidationIssue[]=[]
  for(const item of details){
    if(!item||typeof item!=='object')continue
    const raw=typeof item.message==='string'?item.message:typeof item.msg==='string'?item.msg:''
    if(!raw.trim())continue
    let parts:string[]
    if(typeof item.field==='string')parts=item.field.split('.').filter(Boolean)
    else if(Array.isArray(item.loc))parts=item.loc.filter((part:unknown)=>typeof part==='string'||typeof part==='number').map(String)
    else if(item.field===undefined)parts=[]
    else continue
    while(parts.length&&['body','query','path','header','cookie','__root__'].includes(parts[0]))parts.shift()
    const field=parts.join('.')
    const label=parts.map(part=>fieldNames[part]??(/^[0-9]+$/.test(part)?`第${Number(part)+1}项`:part)).join(' · ')||'表单'
    const message=translatedIssue(raw,typeof item.type==='string'?item.type:'',field,item.ctx&&typeof item.ctx==='object'?item.ctx:{})
    if(!issues.some(issue=>issue.field===field&&issue.message===message))issues.push({field,label,message})
  }
  return issues
}
export function errorMessage(result:ErrorResult):string {
  const messages=validationIssues(result).map(issue=>`${issue.label}：${issue.message}`)
  if(messages.length)return messages.join('；')
  return result.message?.trim()||'操作失败，请刷新页面后重试；仍有问题时请联系管理员并提供请求编号'
}
function withRequestId(message:string,requestId?:string):string {
  return requestId?`${message}（请求编号：${requestId}）`:message
}
export async function api<T=any>(path:string,method='GET',data?:unknown,key?:string):Promise<T>{
  const headers:Record<string,string>={}
  if(method!=='GET') {headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf}
  if(key)headers['Idempotency-Key']=key
  let response:Response
  try {response=await fetch(`/api/v1${path}`,{method,headers,credentials:'same-origin',body:method==='GET'?undefined:JSON.stringify(data??{})})}
  catch{throw new ApiError('NETWORK_ERROR','无法连接服务器，请确认系统已启动并检查网络；恢复连接后请先刷新页面核对操作结果，再决定是否重试',0,null)}
  if(response.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('session-expired'))
  if(response.status===204&&response.ok)return undefined as T
  let result:any
  try{result=await response.json()}
  catch{
    const requestId=response.headers.get('X-Request-ID')||undefined
    throw new ApiError('INVALID_RESPONSE',withRequestId(`服务器返回了无法识别的响应（HTTP ${response.status}），请刷新页面核对操作结果；仍有问题时请联系管理员并提供请求编号`,requestId),response.status,null,requestId)
  }
  if(!response.ok){
    const details=result&&typeof result==='object'?(result.details??(Array.isArray(result.detail)?result.detail:undefined)):undefined
    const message=typeof result?.message==='string'?result.message:typeof result?.detail==='string'&&/[\u4e00-\u9fff]/.test(result.detail)?result.detail:`请求失败（HTTP ${response.status}），请刷新页面后重试；仍有问题时请联系管理员并提供请求编号`
    const code=typeof result?.code==='string'?result.code:'HTTP_ERROR'
    const requestId=typeof result?.request_id==='string'?result.request_id:response.headers.get('X-Request-ID')||undefined
    const description=errorMessage({code,message,details})
    const showRequestId=response.status>=500||(code==='HTTP_ERROR'&&!validationIssues({details}).length)
    throw new ApiError(code,showRequestId?withRequestId(description,requestId):description,response.status,details,requestId)
  }
  return result
}
export const time=(minutes:number)=>`${Math.floor(minutes/60).toString().padStart(2,'0')}:${(minutes%60).toString().padStart(2,'0')}`
export const meetingText=(o:Offering)=>o.meetings.map(m=>`周${'一二三四五六日'[m.weekday-1]} ${time(m.start_minute)}–${time(m.end_minute)} · ${m.room}`).join('；')
export const stateLabel=(state:string)=>({OPEN:'选课开放',CLOSED:'已结束',CANCELLED:'已取消',ENROLLED:'已注册',PENDING:'等待重试',PROCESSING:'发送中',SENT:'已送达'}[state]||state)
export async function allOfferings(semesterId:number){
  const items:Offering[]=[]
  for(let page=1;;page++){
    const batch=await api<{items:Offering[];total:number}>(`/catalog/offerings?semester_id=${semesterId}&page=${page}&page_size=100`)
    items.push(...batch.items)
    if(items.length>=batch.total||!batch.items.length)return items
  }
}

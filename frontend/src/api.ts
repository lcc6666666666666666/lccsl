export interface User { id:number; username:string; name:string; role:'student'|'professor'|'registrar'; csrf_token:string; must_change_password:boolean }
export interface Semester { id:number;name:string;code:string;state:string;version:number;start_date:string;end_date:string;registration_open:string;registration_close:string;completed:boolean;can_register:boolean;close_summary:any }
export interface Offering { id:number;code:string;course_id:number;course_code:string;name:string;department:string;credits:string;professor_id:number|null;professor_name:string|null;capacity:number;enrolled_count:number;remaining:number;fee:string;state:string;eligible:boolean;prerequisites_met:boolean;cancellation_reason:string|null;prerequisites:{code:string;name:string}[];meetings:{weekday:number;start_minute:number;end_minute:number;week_start:number;week_end:number;room:string}[] }
export interface Schedule { version:number;exists:boolean;ever_submitted:boolean;primary:number[];alternates:number[];draft:{primary:number[];alternates:number[]}|null }
let csrf=''
export function setCsrf(value:string){csrf=value}
export class ApiError extends Error { constructor(public code:string,message:string,public status:number,public details:any){super(message)} }
export async function api<T=any>(path:string,method='GET',data?:unknown,key?:string):Promise<T>{
  const headers:Record<string,string>={}
  if(method!=='GET') {headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf}
  if(key)headers['Idempotency-Key']=key
  let response:Response
  try {response=await fetch(`/api/v1${path}`,{method,headers,credentials:'same-origin',body:method==='GET'?undefined:JSON.stringify(data??{})})}
  catch{throw new ApiError('NETWORK_ERROR','无法连接服务器，请检查连接后重试',0,null)}
  const result=await response.json().catch(()=>({message:'服务器返回了无法识别的响应'}))
  if(!response.ok){
    if(response.status===401 && !path.startsWith('/auth/'))window.dispatchEvent(new Event('session-expired'))
    throw new ApiError(result.code,result.message||'操作失败',response.status,result.details)
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

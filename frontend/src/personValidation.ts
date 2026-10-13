export interface PersonForm {
  username:string; name:string; birth_date:string|null; identity_number:string; status:string;
  department:string; graduation_date:string|null; qualification_ids:number[]
}
export type PersonField = keyof PersonForm
export interface PersonFieldError { field:PersonField; message:string }

function validDate(value:unknown):value is string {
  if(typeof value!=='string'||!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)||value.startsWith('0000'))return false
  const date=new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value
}
const trimmed=(value:unknown)=>typeof value==='string'?value.trim():''
const length=(value:string)=>Array.from(value).length
function localToday():string {
  const date=new Date()
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`
}

/** Collect all actionable field errors without echoing an identity number. */
export function personFormErrors(form:PersonForm,kind:'student'|'professor',editing:boolean,today=localToday()):PersonFieldError[] {
  const errors:PersonFieldError[]=[]
  const add=(field:PersonField,message:string)=>{if(!errors.some(error=>error.field===field))errors.push({field,message})}
  const username=trimmed(form.username)
  if(!editing){
    if(!username)add('username','请输入账号，需为3～64位英文字母、数字、点、下划线或短横线')
    else if(username.length<3||username.length>64)add('username',`账号需为3～64位，当前为${length(username)}位；可使用英文字母、数字、点、下划线或短横线`)
    else if(!/^[A-Za-z0-9_.-]+$/.test(username))add('username','账号仅允许英文字母、数字、点、下划线或短横线，不能包含中文、空格或其他符号')
  }
  const name=trimmed(form.name)
  if(!name)add('name','请输入姓名，不能为空或仅包含空格')
  else if(length(name)>80)add('name',`姓名最多80个字符，当前为${length(name)}个，请缩短后重试`)
  if(!form.birth_date)add('birth_date','请选择出生日期，此项必填')
  else if(!validDate(form.birth_date))add('birth_date','出生日期必须是有效日期，请按年-月-日选择，例如2004-01-01')
  else if(form.birth_date>=today)add('birth_date','出生日期必须早于今天，不能选择今天或未来日期')
  const status=trimmed(form.status)
  if(!status)add('status','请输入人员状态，不能为空或仅包含空格')
  else if(length(status)>30)add('status',`人员状态最多30个字符，当前为${length(status)}个`)
  const department=trimmed(form.department)
  if(kind==='professor'&&!department)add('department','教授必须填写所属院系，不能为空或仅包含空格')
  else if(length(department)>80)add('department',`所属院系最多80个字符，当前为${length(department)}个`)
  const identity=trimmed(form.identity_number).toUpperCase()
  if(!identity&&!editing)add('identity_number','请输入18位身份证号码；新增人员时此项必填')
  if(identity){
    if(identity.length!==18)add('identity_number',`身份证号码必须为18位，当前为${length(identity)}位；前17位为数字，末位为数字或X`)
    else if(!/^[1-9][0-9]{16}[0-9X]$/.test(identity))add('identity_number','身份证号码必须为18位半角字符，首位不能为0，前17位为数字，末位为数字或X；请勿填写空格或全角数字')
    else {
      const birth=`${identity.slice(6,10)}-${identity.slice(10,12)}-${identity.slice(12,14)}`
      if(!validDate(birth))add('identity_number','身份证号码中的出生日期无效，请核对第7～14位是否为真实存在的年月日')
      else if(birth>=today)add('identity_number','身份证号码中的出生日期必须早于今天，请核对第7～14位')
      else {
        const weights=[7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
        const sum=weights.reduce((total,weight,index)=>total+Number(identity[index])*weight,0)
        if(identity[17]!=='10X98765432'[sum%11])add('identity_number','身份证号码校验码不正确，请核对完整号码及末位；末位为X时可输入大小写X')
        else if(validDate(form.birth_date)&&birth!==form.birth_date)add('identity_number','出生日期与身份证号码不一致，请核对所选出生日期及身份证第7～14位')
      }
    }
  }
  if(kind==='student'&&form.graduation_date){
    if(!validDate(form.graduation_date))add('graduation_date','毕业日期必须是有效日期，请按年-月-日选择；尚未确定时可以留空')
    else if(validDate(form.birth_date)&&form.graduation_date<=form.birth_date)add('graduation_date','毕业日期必须晚于出生日期；尚未确定时可以留空')
  }
  if(!Array.isArray(form.qualification_ids))add('qualification_ids','任课资格格式无效，请重新选择课程')
  else if(form.qualification_ids.length>100)add('qualification_ids',`任课资格最多选择100门课程，当前选择${form.qualification_ids.length}门`)
  else if(kind==='student'&&form.qualification_ids.length)add('qualification_ids','学生不能设置任课资格，请清空任课课程')
  return errors
}

/** Compatibility for callers that only need the first error. */
export function personFormError(form:PersonForm,kind:'student'|'professor',editing:boolean,today=localToday()):string|null {
  return personFormErrors(form,kind,editing,today)[0]?.message??null
}

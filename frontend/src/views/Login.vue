<script setup lang="ts">
import {computed,reactive,ref} from 'vue'
import {useRoute} from 'vue-router'
import {useAuth} from '../store'
import {router,roleHome} from '../router'
import {api} from '../api'
import {ElMessage} from 'element-plus'
const auth=useAuth(),route=useRoute()
const changing=computed(()=>route.path==='/password')
const form=reactive({username:'',password:'',newPassword:'',confirm:''}),busy=ref(false),error=ref('')
async function submit(){
  error.value='';busy.value=true
  try{
    if(changing.value){
      if(form.newPassword.length<12)throw new Error(`新密码至少需要12个字符，当前为${form.newPassword.length}个字符，请补充后再提交。`)
      if(form.newPassword!==form.confirm)throw new Error('确认新密码与新密码不一致，请重新输入相同的密码；可点击密码框中的显示按钮进行核对。')
      await api('/auth/password','POST',{old_password:form.password,new_password:form.newPassword})
      form.password='';form.newPassword='';form.confirm=''
      auth.setUser(null);ElMessage.success('密码已更新，请使用新密码登录');await router.push('/login')
    }else{await auth.login(form.username,form.password);await router.push(auth.user!.must_change_password?'/password':roleHome(auth.user!.role))}
  }catch(e:any){error.value=e.message}finally{busy.value=false}
}
</script>
<template><div class="login-page">
  <section class="login-story"><div class="brand light"><span class="brand-symbol">学</span><span>学程<small>COURSE REGISTRATION</small></span></div>
    <div class="story-copy"><div class="eyebrow">每一次选择，都是新的开始</div><h1>选好每一门课，<br>走好下一程。</h1><p>从课程选择到学期成绩，<br>在这里，清晰安排你的学习旅程。</p>
      <div class="illustration"><div class="floating-label">YOUR NEXT CHAPTER</div><div class="sample-course"><span>01</span><div><b>软件工程导论</b><small>探索设计与实现之间的可能</small></div><i>↗</i></div><div class="sample-course secondary"><span>02</span><div><b>数据库原理</b><small>让每一份数据井然有序</small></div><i>↗</i></div></div>
    </div><div class="story-footer">计算机科学与技术学院 · 课程注册服务</div>
  </section>
  <section class="login-panel"><div class="login-form"><div class="eyebrow">欢迎来到学程</div><h2>{{changing?'设置你的安全密码':'登录课程注册系统'}}</h2><p class="muted">{{changing?'初始密码需先修改，才能进入工作台。':'使用学校分配的账户，进入你的专属工作台。'}}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb"/>
    <form @submit.prevent="submit"><label v-if="!changing">账号<el-input v-model="form.username" aria-label="账号" autocomplete="username" placeholder="请输入账号" size="large" required maxlength="64"/></label>
      <label>{{changing?'当前密码':'密码'}}<el-input v-model="form.password" :aria-label="changing?'当前密码':'密码'" type="password" show-password autocomplete="current-password" size="large" placeholder="请输入密码" required maxlength="128"/></label>
      <template v-if="changing"><label>新密码<el-input v-model="form.newPassword" aria-label="新密码" type="password" show-password autocomplete="new-password" size="large" placeholder="至少12个字符" required maxlength="128"/></label><label>确认新密码<el-input v-model="form.confirm" aria-label="确认新密码" type="password" show-password autocomplete="new-password" size="large" required maxlength="128"/></label></template>
      <el-button type="primary" size="large" class="full-width" native-type="submit" :aria-label="changing?'更新密码':'登录'" :loading="busy">{{changing?'更新密码':'登录'}} <span class="button-arrow">→</span></el-button>
    </form><p class="login-hint">账号由教务管理员统一维护。<br>如需重置密码，请联系教务管理员。</p>
    <el-button v-if="changing&&!auth.user?.must_change_password" text @click="router.push(roleHome(auth.user?.role||'student'))">返回工作台</el-button>
  </div></section>
</div></template>

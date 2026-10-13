<script setup lang="ts">
import {computed,ref} from 'vue'
import {useRoute} from 'vue-router'
import {router,roleHome} from './router'
import {useAuth} from './store'
import {Reading,User,School,Calendar,Document,Connection,Lock,SwitchButton} from '@element-plus/icons-vue'
const route=useRoute(),auth=useAuth()
const bare=computed(()=>['/login','/password'].includes(route.path))
const roleNames={student:'学生工作台',professor:'教授工作台',registrar:'教务管理台'}
const menus=computed(()=>auth.user?.role==='registrar'?[['/registrar','学期与选课',Calendar],['/registrar/catalog','课程文件导入',Reading],['/registrar/students','学生信息',User],['/registrar/professors','教授信息',School],['/registrar/billing','计费任务',Connection],['/registrar/audit','操作记录',Document]]:[[roleHome(auth.user?.role||'student'),auth.user?.role==='student'?'我的选课与成绩':'任课与成绩管理',Reading]])
const logoutPending=ref(false),logoutError=ref('')
async function logout(){if(logoutPending.value)return;logoutPending.value=true;logoutError.value='';try{await auth.logout();await router.push('/login')}catch(e:any){logoutError.value=`退出登录未完成：${e.message}。恢复连接后请再次点击“退出”。`}finally{logoutPending.value=false}}
</script>
<template>
  <router-view v-if="bare"/>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <a class="brand" :href="roleHome(auth.user?.role||'student')"><span class="brand-symbol">学</span><span>学程<small>COURSE REGISTRATION</small></span></a>
      <div class="nav-label">{{auth.user?roleNames[auth.user.role]:''}}</div>
      <nav><router-link v-for="m in menus" :key="String(m[0])" :to="String(m[0])" :class="{active:route.path===m[0]}"><el-icon><component :is="m[2]"/></el-icon>{{m[1]}}</router-link></nav>
      <div class="sidebar-bottom"><el-icon><Lock/></el-icon><span>连接你的下一段学习旅程<small>课程注册系统 · 2026</small></span></div>
    </aside>
    <section class="main-area">
      <header class="topbar"><span><i class="status-dot"/>校园课程服务</span><div class="user-area"><span class="avatar">{{auth.user?.name.slice(0,1)}}</span><span>{{auth.user?.name}}<small>{{auth.user?.username}}</small></span><el-button text @click="router.push('/password')">修改密码</el-button><el-button text :icon="SwitchButton" :loading="logoutPending" @click="logout">退出</el-button></div></header>
      <main class="page-content"><el-alert v-if="logoutError" :title="logoutError" type="error" :closable="false" show-icon class="mb"/><router-view/></main>
      <footer>学程 · 让每一次选择都有清晰的方向</footer>
    </section>
  </div>
</template>

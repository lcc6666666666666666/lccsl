import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { router } from './router'
import { useAuth } from './store'
const app=createApp(App)
app.use(createPinia())
await useAuth().restore()
app.use(router).use(ElementPlus,{locale:zhCn}).mount('#app')
window.addEventListener('session-expired',()=>{useAuth().setUser(null);router.replace('/login')})

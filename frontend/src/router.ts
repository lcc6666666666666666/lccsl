import { createRouter,createWebHistory } from 'vue-router'
import { useAuth } from './store'
import Login from './views/Login.vue'
import Student from './views/Student.vue'
import Professor from './views/Professor.vue'
import Registrar from './views/Registrar.vue'
import CatalogImport from './views/CatalogImport.vue'
export const roleHome=(role:string)=>`/${role}`
export const router=createRouter({history:createWebHistory(),routes:[
  {path:'/login',component:Login},{path:'/password',component:Login},
  {path:'/student',component:Student,meta:{role:'student'}},
  {path:'/professor',component:Professor,meta:{role:'professor'}},
  {path:'/registrar/catalog',component:CatalogImport,meta:{role:'registrar'}},
  {path:'/registrar/:tab?',component:Registrar,meta:{role:'registrar'}},
  {path:'/:pathMatch(.*)*',redirect:()=>useAuth().user?roleHome(useAuth().user!.role):'/login'}
]})
router.beforeEach(to=>{
  const auth=useAuth()
  if(!auth.user)return to.path==='/login'?true:'/login'
  if(auth.user.must_change_password)return to.path==='/password'?true:'/password'
  if(to.path==='/login')return roleHome(auth.user.role)
  if(to.meta.role && to.meta.role!==auth.user.role)return roleHome(auth.user.role)
  return true
})

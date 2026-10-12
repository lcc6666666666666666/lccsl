import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api,setCsrf,type User } from './api'
export const useAuth=defineStore('auth',()=>{
  const user=ref<User|null>(null)
  function setUser(value:User|null){user.value=value;setCsrf(value?.csrf_token||'')}
  async function restore(){try{setUser(await api<User>('/auth/me'))}catch{setUser(null)}}
  async function login(username:string,password:string){setUser(await api<User>('/auth/login','POST',{username,password}))}
  async function logout(){try{await api('/auth/logout','POST')}finally{setUser(null)}}
  return{user,setUser,restore,login,logout}
})

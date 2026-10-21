import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import RequirementView from './views/RequirementView.vue'
import AdminView from './views/AdminView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    // props: true -> URL 의 :id 가 컴포넌트의 id prop 으로 들어간다
    { path: '/requirements/:id', component: RequirementView, props: true },
    { path: '/admin', component: AdminView },
  ],
})

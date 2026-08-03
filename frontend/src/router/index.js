import { createRouter, createWebHistory } from 'vue-router'


const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('@/views/Layout.vue'),
    meta: { requiresAuth: true },
    redirect: '/mails',
    children: [
      {
        path: '/mails',
        name: 'MailList',
        component: () => import('@/views/MailList.vue')
      },
      {
        path: '/mails/:id',
        name: 'MailDetail',
        component: () => import('@/views/MailDetail.vue')
      },
      {
        path: '/knowledge/new',
        name: 'KnowledgeCreate',
        component: () => import('@/views/KnowledgeEditor.vue')
      },
      {
        path: '/knowledge/edit/:filename',
        name: 'KnowledgeEdit',
        component: () => import('@/views/KnowledgeEditor.vue')
      },
      {
        path: '/knowledge',
        name: 'Knowledge',
        component: () => import('@/views/Knowledge.vue')
      },
      {
        path: '/settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue')
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach((to, from, next) => {
  const hasToken = Boolean(localStorage.getItem('auth_token'))
  
  if (to.meta.requiresAuth && !hasToken) {
    next('/login')
  } else if (to.path === '/login' && hasToken) {
    next('/')
  } else {
    next()
  }
})

export default router

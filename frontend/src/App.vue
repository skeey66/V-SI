<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <header class="topbar">
    <RouterLink to="/" class="brand"><b>V-SI</b><small>가상 SI 회사</small></RouterLink>
    <nav>
      <RouterLink to="/">작업 요청</RouterLink>
      <RouterLink to="/admin">관리자</RouterLink>
    </nav>
  </header>
  <main class="container">
    <!-- 같은 컴포넌트라도 id 가 바뀌면 새로 만들도록 key 지정 -->
    <RouterView :key="$route.fullPath" />
  </main>
</template>

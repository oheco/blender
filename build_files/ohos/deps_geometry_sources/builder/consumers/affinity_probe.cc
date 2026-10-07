#include <sched.h>
#include <pthread.h>
#include <unistd.h>
#include <cerrno>
#include <cstdio>
#ifndef __OHOS__
#error Expected real OHOS target macro
#endif
static int worker_status=0;
static void* worker(void*) {
  cpu_set_t initial;CPU_ZERO(&initial);
  if(sched_getaffinity(0,sizeof(initial),&initial)!=0 || CPU_COUNT(&initial)<1) { worker_status=1;return nullptr; }
  int cpu=-1;
  for(int i=0;i<CPU_SETSIZE;++i)if(CPU_ISSET(i,&initial)){cpu=i;break;}
  cpu_set_t one;CPU_ZERO(&one);CPU_SET(cpu,&one);
  if(sched_setaffinity(0,sizeof(one),&one)!=0) { worker_status=2;return nullptr; }
  cpu_set_t actual;CPU_ZERO(&actual);
  if(sched_getaffinity(0,sizeof(actual),&actual)!=0 || CPU_COUNT(&actual)!=1 || !CPU_ISSET(cpu,&actual)) {worker_status=3;return nullptr;}
  if(sched_setaffinity(0,sizeof(initial),&initial)!=0) {worker_status=4;return nullptr;}
  std::printf("worker affinity restricted/read/restored cpu=%d PASS\n",cpu);
  return nullptr;
}
int main() {
  cpu_set_t mask;CPU_ZERO(&mask);
  int result=sched_getaffinity(0,sizeof(mask),&mask);
  long online=sysconf(_SC_NPROCESSORS_ONLN);
  std::printf("OHOS sched_getaffinity(0) result=%d errno=%d count=%d online=%ld\n",result,errno,CPU_COUNT(&mask),online);
  if(result!=0 || CPU_COUNT(&mask)<1 || online<1)return 1;
  pthread_t t;if(pthread_create(&t,nullptr,worker,nullptr)!=0)return 2;
  if(pthread_join(t,nullptr)!=0)return 3;
  return worker_status;
}

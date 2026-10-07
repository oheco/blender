#include <arm_neon.h>
#include <pthread.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <thread>
#include <typeinfo>
#define S1(x) #x
#define S(x) S1(x)
static std::atomic<int> count{0};
static void* worker(void*) { for(int i=0;i<1000;++i) count.fetch_add(1); return nullptr; }
int main() {
#ifndef REPLAY_EXPECTED_CLANG_MAJOR
#define REPLAY_EXPECTED_CLANG_MAJOR 20
#endif
  static_assert(__clang_major__ == REPLAY_EXPECTED_CLANG_MAJOR);
  static_assert(__cplusplus >= 202002L);
  static_assert(sizeof(void*) == 8);
#ifndef __aarch64__
#error actual AArch64 required
#endif
  if(std::strcmp(S(_LIBCPP_ABI_NAMESPACE),"__n1")!=0) return 1;
  pthread_t t;
  if(pthread_create(&t,nullptr,worker,nullptr)!=0) return 2;
  std::thread cpp([]{ worker(nullptr); });
  if(pthread_join(t,nullptr)!=0) return 3;
  cpp.join();
  auto a=vdupq_n_f32(2.0f); auto b=vdupq_n_f32(3.0f);
  if(vgetq_lane_f32(vmulq_f32(a,b),2)!=6.0f || count!=2000) return 4;
  try { throw std::runtime_error("abi"); }
  catch(const std::exception& e) { if(std::strcmp(e.what(),"abi")!=0) return 5; }
  std::printf("Clang%d libc++%d ABI=%s ARM_NEON pthread/std::thread=2000 exceptions=PASS\n",__clang_major__,_LIBCPP_VERSION,S(_LIBCPP_ABI_NAMESPACE));
  return 0;
}

#include <oneapi/tbb/parallel_for.h>
#include <oneapi/tbb/parallel_reduce.h>
#include <oneapi/tbb/blocked_range.h>
#include <oneapi/tbb/global_control.h>
#include <oneapi/tbb/task_group.h>
#include <oneapi/tbb/task_arena.h>
#include <oneapi/tbb/scalable_allocator.h>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <thread>
int main(){
 namespace tbb=oneapi::tbb;
 tbb::global_control limit(tbb::global_control::max_allowed_parallelism,2);
 tbb::task_arena arena(2);
 std::atomic<bool> ready{false},release{false};
 std::thread::id worker,main_thread=std::this_thread::get_id();
 arena.execute([&]{
  tbb::task_group group;
  group.run([&]{worker=std::this_thread::get_id();ready.store(true,std::memory_order_release);while(!release.load(std::memory_order_acquire))std::this_thread::yield();});
  auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
  while(!ready.load(std::memory_order_acquire)&&std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
  release.store(true,std::memory_order_release);group.wait();
 });
 assert(ready&&worker!=main_thread);
 std::atomic<long long> count{0};
 tbb::parallel_for(0,100000,[&](int i){count.fetch_add(i,std::memory_order_relaxed);});
 assert(count==4999950000LL);
 auto sum=tbb::parallel_reduce(tbb::blocked_range<int>(0,100000),0LL,[](const auto &r,long long x){for(int i=r.begin();i<r.end();++i)x+=i;return x;},[](long long a,long long b){return a+b;});
 assert(sum==count.load());
 bool caught=false;
 try{tbb::parallel_for(0,10000,[](int i){if(i==777)throw std::runtime_error("expected task exception");});}catch(const std::runtime_error &){caught=true;}
 assert(caught);
 tbb::parallel_for(0,1000,[](int){void *p=scalable_malloc(4096);assert(p);static_cast<char*>(p)[4095]=42;scalable_free(p);});
 std::printf("PASS oneTBB real worker != main, atomic/reduce=%lld, task exception/cancellation, scalable allocator; no pthread_cancel API claim\n",sum);
}

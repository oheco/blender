#include <oneapi/tbb/global_control.h>
#include <oneapi/tbb/parallel_for.h>
#include <oneapi/tbb/version.h>
#include <atomic>
#include <cstdio>
#include <stdexcept>
static_assert(TBB_VERSION_MAJOR==2022 && TBB_VERSION_MINOR==3);
static_assert(_LIBCPP_VERSION==15004);
int main(){
  oneapi::tbb::global_control threads(oneapi::tbb::global_control::max_allowed_parallelism,2);
  std::atomic<int> sum{0};
  oneapi::tbb::parallel_for(0,128,[&](int i){sum.fetch_add(i);});
  if(sum!=8128)return 1;
  bool caught=false;
  try {oneapi::tbb::parallel_for(0,32,[](int i){if(i==7)throw std::runtime_error("native TBB exception");});}
  catch(const std::runtime_error&){caught=true;}
  if(!caught)return 2;
  std::printf("Actual pinned TBB2022.3 native tasks/sum/thread2/exception=PASS\n");
}

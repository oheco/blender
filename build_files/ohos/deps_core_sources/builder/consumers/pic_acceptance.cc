#include <cassert>
#include <cstring>
#include <exception>
#include <iostream>
extern "C" int core_pic_run();
extern "C" const char* core_abi_name();
extern "C" void core_abi_exception();
int main() {
  assert(std::strcmp(core_abi_name(),"__n1")==0);
  assert(core_pic_run()==0);
  bool caught=false;
  try {core_abi_exception();}
  catch(const std::exception& error) {caught=std::strcmp(error.what(),"core bridge ABI exception")==0;}
  assert(caught);
  std::cout<<"Embree+Ceres+Eigen static PIC shared bridge and __n1 exception ABI=PASS\n";
}

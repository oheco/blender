#include <embree4/rtcore.h>
#include <ceres/ceres.h>
#include <Eigen/Dense>
#include <stdexcept>
#define S1(x) #x
#define S(x) S1(x)
struct Residual {template<class T> bool operator()(const T* x,T* r)const {r[0]=x[0]-T(7);return true;}};
extern "C" const char* core_abi_name() {return S(_LIBCPP_ABI_NAMESPACE);}
extern "C" void core_abi_exception() {throw std::runtime_error("core bridge ABI exception");}
extern "C" int core_pic_run() {
  Eigen::Matrix2d a;a<<2,0,0,4;
  if((a.inverse()*Eigen::Vector2d(2,8)-Eigen::Vector2d(1,2)).norm()>1e-12)return 1;
  RTCDevice device=rtcNewDevice("threads=2");if(!device)return 2;
  RTCScene scene=rtcNewScene(device);if(!scene)return 3;
  rtcCommitScene(scene);
  if(rtcGetDeviceError(device)!=RTC_ERROR_NONE)return 4;
  rtcReleaseScene(scene);rtcReleaseDevice(device);
  double x=0;ceres::Problem problem;
  problem.AddResidualBlock(new ceres::AutoDiffCostFunction<Residual,1,1>(new Residual),nullptr,&x);
  ceres::Solver::Options options;options.linear_solver_type=ceres::DENSE_QR;options.logging_type=ceres::SILENT;
  ceres::Solver::Summary summary;ceres::Solve(options,&problem,&summary);
  return summary.IsSolutionUsable()&&std::abs(x-7)<1e-7?0:5;
}

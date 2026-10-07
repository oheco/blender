#include <ceres/ceres.h>
#include <ceres/covariance.h>
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
struct Fit {
  double x,y;
  template<class T> bool operator()(const T* const p,T* r) const {r[0]=p[0]*T(x)+p[1]-T(y);return true;}
};
struct BadEvaluation final:ceres::SizedCostFunction<1,1> {
  bool Evaluate(double const* const*,double*,double**)const override {return false;}
};
static void fit(ceres::LinearSolverType linear) {
  double p[2]={0,0};ceres::Problem problem;
  for(int i=0;i<120;++i) {
    double x=(i-60)/10.0;
    problem.AddResidualBlock(new ceres::AutoDiffCostFunction<Fit,1,2>(new Fit{x,2.5*x-1.75}),new ceres::HuberLoss(1),p);
  }
  ceres::Solver::Options options;options.linear_solver_type=linear;options.num_threads=2;options.max_num_iterations=80;
  options.sparse_linear_algebra_library_type=ceres::EIGEN_SPARSE;
  options.function_tolerance=1e-14;options.gradient_tolerance=1e-14;options.parameter_tolerance=1e-14;
  ceres::Solver::Summary summary;ceres::Solve(options,&problem,&summary);
  assert(summary.IsSolutionUsable());assert(summary.termination_type==ceres::CONVERGENCE);
  assert(std::abs(p[0]-2.5)<1e-9 && std::abs(p[1]+1.75)<1e-9);
  assert(summary.final_cost<1e-16);
  ceres::Covariance::Options co;co.algorithm_type=ceres::DENSE_SVD;
  ceres::Covariance covariance(co);std::vector<std::pair<const double*,const double*>> blocks{{p,p}};
  assert(covariance.Compute(blocks,&problem));double cov[4];assert(covariance.GetCovarianceBlock(p,p,cov));
  assert(cov[0]>0 && cov[3]>0 && std::abs(cov[1]-cov[2])<1e-12);
  std::cout<<ceres::LinearSolverTypeToString(linear)<<" residual/AutoDiff/Huber/thread2/covariance=PASS "<<summary.BriefReport()<<"\n";
}
int main() {
  assert(ceres::IsSparseLinearAlgebraLibraryTypeAvailable(ceres::EIGEN_SPARSE));
  fit(ceres::DENSE_QR);fit(ceres::SPARSE_NORMAL_CHOLESKY);
  ceres::Solver::Options invalid;invalid.max_num_iterations=-1;std::string error;
  assert(!invalid.IsValid(&error) && !error.empty());
  double x=1;ceres::Problem bad;bad.AddResidualBlock(new BadEvaluation,nullptr,&x);
  ceres::Solver::Options options;options.linear_solver_type=ceres::DENSE_QR;options.logging_type=ceres::SILENT;
  ceres::Solver::Summary summary;ceres::Solve(options,&bad,&summary);
  assert(summary.termination_type==ceres::FAILURE && !summary.IsSolutionUsable() && !summary.message.empty());
  assert(x==1);
  std::cout<<"Ceres invalid-options and failed-cost evaluation=PASS\n";
}

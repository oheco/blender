#include <Eigen/Dense>
#include <Eigen/Sparse>
#include <Eigen/QR>
#include <tbb/global_control.h>
#include <cassert>
#include <cmath>
#include <iostream>
int main() {
  tbb::global_control threads(tbb::global_control::max_allowed_parallelism,2);
  Eigen::Matrix3d a;
  a<<4,1,0, 1,3,1, 0,1,2;
  Eigen::Vector3d expected(1,2,3);
  Eigen::Vector3d solution=a.colPivHouseholderQr().solve(a*expected);
  assert((solution-expected).norm()<1e-12);
  Eigen::SelfAdjointEigenSolver<Eigen::Matrix3d> eig(a);
  assert(eig.info()==Eigen::Success);
  assert((a*eig.eigenvectors()-eig.eigenvectors()*eig.eigenvalues().asDiagonal()).norm()<1e-12);
  const int n=12000;
  Eigen::SparseMatrix<double,Eigen::RowMajor> s(n,n);
  std::vector<Eigen::Triplet<double>> entries;
  entries.reserve(n*3);
  for(int i=0;i<n;++i) {
    entries.emplace_back(i,i,3.0);
    if(i>0) entries.emplace_back(i,i-1,-1.0);
    if(i+1<n) entries.emplace_back(i,i+1,-1.0);
  }
  s.setFromTriplets(entries.begin(),entries.end());
  assert(s.nonZeros()>20000);
  Eigen::Matrix<double,Eigen::Dynamic,3,Eigen::RowMajor> row=Eigen::Matrix<double,Eigen::Dynamic,3,Eigen::RowMajor>::Ones(n,3);
  row.col(1)*=2;row.col(2)*=3;
  Eigen::MatrixXd col=row;
  Eigen::MatrixXd outCol=s*col;
  Eigen::Matrix<double,Eigen::Dynamic,3,Eigen::RowMajor> outRow=s*row;
  assert((outCol-outRow).norm()<1e-12);
  for(int i=0;i<n;++i)for(int j=0;j<3;++j)
    assert(std::abs(outCol(i,j)-(j+1)*(i==0||i==n-1?2.0:1.0))<1e-12);
  Eigen::SparseMatrix<double> sc=s;
  Eigen::SimplicialLDLT<Eigen::SparseMatrix<double>> solver(sc);
  assert(solver.info()==Eigen::Success);
  Eigen::VectorXd x=solver.solve(outCol.col(0));
  assert(solver.info()==Eigen::Success);
  assert((x-Eigen::VectorXd::Ones(n)).norm()<1e-10);
  std::cout<<"Eigen dense QR/eigenpairs, sparse TBB row+column products nnz="<<s.nonZeros()<<", LDLT=PASS\n";
}

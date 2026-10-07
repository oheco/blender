#include <fftw3.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <numbers>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
static int checks=0;
static void require(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static void near(double a,double b,double eps,const std::string& why){require(std::isfinite(a)&&std::abs(a-b)<=eps,why);}
template<class T> struct FFT;
#define FFT_TRAIT(TYPE, PREFIX) \
template<> struct FFT<TYPE>{ \
 using Complex=PREFIX##_complex;using Plan=PREFIX##_plan; \
 static int init(){return PREFIX##_init_threads();} \
 static void threads(int n){PREFIX##_plan_with_nthreads(n);} \
 static Plan complex(int n,Complex* in,Complex* out,int sign){return PREFIX##_plan_dft_1d(n,in,out,sign,FFTW_ESTIMATE);} \
 static Plan r2c(int n,TYPE* in,Complex* out){return PREFIX##_plan_dft_r2c_1d(n,in,out,FFTW_ESTIMATE);} \
 static Plan c2r(int n,Complex* in,TYPE* out){return PREFIX##_plan_dft_c2r_1d(n,in,out,FFTW_ESTIMATE);} \
 static void execute(Plan p){PREFIX##_execute(p);} \
 static void destroy(Plan p){PREFIX##_destroy_plan(p);} \
 static void cleanup(){PREFIX##_cleanup_threads();} \
 static const char* version(){return PREFIX##_version;} \
};
FFT_TRAIT(double,fftw)
FFT_TRAIT(float,fftwf)
#undef FFT_TRAIT

template<class T> static void precision(){
 using F=FFT<T>;constexpr int n=4096;constexpr double eps=std::is_same_v<T,float>?4.e-4:1.e-10;
 require(std::string(F::version()).starts_with("fftw-3.3.10"),"runtime FFTW actual pinned version");require(F::init()!=0,"real FFTW pthread initialization");F::threads(2);
 std::vector<std::array<T,2>> input(n),frequency(n),inverse(n),saved(n);
 auto ptr=[](auto& v){return reinterpret_cast<typename F::Complex*>(v.data());};
 require(F::complex(0,ptr(input),ptr(frequency),FFTW_FORWARD)==nullptr,"zero-length complex plan rejected");
 T invalid_real[1]={0};
 require(F::r2c(0,invalid_real,ptr(frequency))==nullptr,"zero-length real-to-complex plan rejected");
 require(F::c2r(0,ptr(frequency),invalid_real)==nullptr,"zero-length complex-to-real plan rejected");
 for(int i=0;i<n;++i){double t=2*std::numbers::pi*i/n;input[i]={T(-2.5+32*std::cos(7*t)),T(.125+std::sin(3*t))};}saved=input;
 auto forward=F::complex(n,ptr(input),ptr(frequency),FFTW_FORWARD);auto backward=F::complex(n,ptr(frequency),ptr(inverse),FFTW_BACKWARD);
 require(forward&&backward,"complex forward/inverse plans");F::execute(forward);F::execute(backward);
 for(int i=0;i<n;++i)for(int c=0;c<2;++c)near(double(inverse[i][c])/n,saved[i][c],eps,"complex negative/HDR roundtrip");
 near(double(frequency[0][0])/n,-2.5,eps,"complex real DC coefficient");near(double(frequency[0][1])/n,.125,eps,"complex imaginary DC coefficient");
 near(double(frequency[7][0])/n,16,eps,"known complex cosine coefficient");F::destroy(forward);F::destroy(backward);
 std::fill(input.begin(),input.end(),std::array<T,2>{0,0});input[0][0]=1;
 auto impulse=F::complex(n,ptr(input),ptr(frequency),FFTW_FORWARD);require(impulse!=nullptr,"impulse plan");F::execute(impulse);
 for(int i=0;i<n;++i){near(frequency[i][0],1,eps,"known impulse spectrum real");near(frequency[i][1],0,eps,"known impulse spectrum imaginary");}F::destroy(impulse);
 std::vector<T> real(n),real_inverse(n);std::vector<std::array<T,2>> packed(n/2+1);
 for(int i=0;i<n;++i)real[i]=T(1+std::sin(2*std::numbers::pi*5*i/n));auto original=real;
 auto r2c=F::r2c(n,real.data(),ptr(packed));auto c2r=F::c2r(n,ptr(packed),real_inverse.data());require(r2c&&c2r,"real/packed-complex plans");F::execute(r2c);
 near(double(packed[0][0])/n,1,eps,"real FFT DC");near(double(packed[5][0])/n,0,eps,"real FFT sine real coefficient");near(double(packed[5][1])/n,-.5,eps,"real FFT sine imaginary coefficient");
 F::execute(c2r);for(int i=0;i<n;++i)near(double(real_inverse[i])/n,original[i],eps,"real FFT inverse normalization");F::destroy(r2c);F::destroy(c2r);F::cleanup();
 std::cout<<"PASS FFTW "<<(std::is_same_v<T,float>?"float":"double")<<" complex/real forward-inverse known spectra + pthread plans version="<<F::version()<<"\n";
}
int main(){try{std::cout<<"ABI libc++="<<_LIBCPP_VERSION<<"\n";precision<double>();precision<float>();std::cout<<"ALL PASS fftw checks="<<checks<<"\n";return 0;}catch(const std::exception& e){std::cerr<<"FAIL fftw after checks="<<checks<<": "<<e.what()<<"\n";return 1;}}

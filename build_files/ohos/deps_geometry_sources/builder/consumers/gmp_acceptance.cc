#include <gmpxx.h>
#include <cassert>
#include <cstdio>
#include <vector>
struct P{mpq_class x,y;};
static mpq_class q(const char *s){mpq_class r(s);r.canonicalize();return r;}
static mpq_class orient(const P&a,const P&b,const P&c){return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);}
static mpq_class area(const std::vector<P>&p){mpq_class r=0;for(size_t i=0;i<p.size();++i){const auto&a=p[i];const auto&b=p[(i+1)%p.size()];r+=a.x*b.y-a.y*b.x;}return abs(r)/2;}
static std::vector<P> clip(const std::vector<P>&poly,int axis,const mpq_class&edge,bool lower){
 std::vector<P> out;
 auto coordinate=[&](const P&p)->const mpq_class&{return axis==0?p.x:p.y;};
 auto inside=[&](const P&p){return lower?coordinate(p)>=edge:coordinate(p)<=edge;};
 for(size_t i=0;i<poly.size();++i){P a=poly[i],b=poly[(i+1)%poly.size()];bool ia=inside(a),ib=inside(b);if(ia)out.push_back(a);if(ia!=ib){mpq_class t=(edge-coordinate(a))/(coordinate(b)-coordinate(a));out.push_back({a.x+t*(b.x-a.x),a.y+t*(b.y-a.y)});}}
 return out;
}
static void multiprecision_roundtrip(){
 mpz_class a;mpz_ui_pow_ui(a.get_mpz_t(),2,521);a+=123456789;
 mpz_class b;mpz_ui_pow_ui(b.get_mpz_t(),3,257);b+=98765;
 for(int i=0;i<32;++i){
  mpz_class product=a*b,quotient=product/a,remainder=product%a;assert(quotient==b&&remainder==0);
  mpz_class decoded(product.get_str(16),16);assert(decoded==product);
  unsigned char bytes[1024];size_t count=0;mpz_export(bytes,&count,1,1,1,0,product.get_mpz_t());
  mpz_class imported;mpz_import(imported.get_mpz_t(),count,1,1,1,0,bytes);assert(imported==product);
  mpz_class modulus=a+2,power,reference=1;mpz_powm_ui(power.get_mpz_t(),b.get_mpz_t(),17,modulus.get_mpz_t());
  for(int j=0;j<17;++j)reference=(reference*b)%modulus;assert(power==reference);
  mpf_class high(product,2048);mpz_class exact;mpz_set_f(exact.get_mpz_t(),high.get_mpf_t());assert(exact==product);
  a+=2;b=b*7+1;
 }
 assert(q("-14/42")==q("-1/3"));
 std::puts("PASS GMP multiprecision C/C++ decimal/hex/import/export/modular/arithmetic/high-precision float roundtrips=32");
}
int main(){
 multiprecision_roundtrip();
 mpz_class big;mpz_ui_pow_ui(big.get_mpz_t(),10,40);mpq_class origin(big);
 std::vector<P>a={{origin,origin},{origin+1,origin},{origin+1,origin+1},{origin,origin+1}};
 std::vector<P>b={{origin+q("1/2"),origin},{origin+q("3/2"),origin},{origin+q("3/2"),origin+1},{origin+q("1/2"),origin+1}};
 auto intersection=clip(a,0,origin+q("1/2"),true);intersection=clip(intersection,0,origin+q("3/2"),false);intersection=clip(intersection,1,origin,true);intersection=clip(intersection,1,origin+1,false);
 assert(intersection.size()==4);assert(area(a)==1&&area(b)==1);assert(area(intersection)==q("1/2"));
 assert(area(a)+area(b)-area(intersection)==q("3/2"));assert(area(a)-area(intersection)==q("1/2"));
 mpz_class denom;mpz_ui_pow_ui(denom.get_mpz_t(),10,80);mpq_class epsilon(mpz_class(1),denom);epsilon.canonicalize();
 P p{origin,origin},r{origin+1,origin+1},s{origin+2,origin+2+epsilon};
 assert(orient(p,r,s)>0);s.y=origin+2-epsilon;assert(orient(p,r,s)<0);s.y=origin+2;assert(orient(p,r,s)==0);
 mpq_class canonical=q("2/6")+q("1/3");assert(canonical==q("2/3"));
 std::printf("PASS GMP %s: exact rational clipping intersection=1/2 union area=3/2 difference=1/2 at 10^40 origin; orientation +/-10^-80, collinear and normalization. Blender Boolean solver NOT tested\n",gmp_version);
}

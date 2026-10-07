#include <stdexcept>
#include <string>
#include <typeinfo>
#include <format>
struct B{virtual ~B()=default;}; struct D:B{};
int main(){D d; B* b=&d; if(dynamic_cast<D*>(b)!=&d || typeid(*b)!=typeid(D))return 1; bool ordinary=false,format=false; try{throw std::runtime_error("ohos");}catch(const std::runtime_error& e){ordinary=std::string(e.what())=="ohos";} int value=1; try{(void)std::vformat("{",std::make_format_args(value));}catch(const std::format_error&){format=true;} return !ordinary || !format;}

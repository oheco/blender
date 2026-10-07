#include <openvdb/openvdb.h>
#include <fftw3.h>
// This real shared module is a PIC link gate. It is signed but never dlopened;
// a second statically linked C++ runtime needs separate ABI ownership design.
extern "C" int volume_pic_symbols(){
 openvdb::initialize();
 auto grid=openvdb::FloatGrid::create(0);
 return int(grid->activeVoxelCount())+fftw_init_threads()+fftwf_init_threads();
}

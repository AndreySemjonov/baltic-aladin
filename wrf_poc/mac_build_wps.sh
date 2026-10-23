#!/usr/bin/env bash
# WPS 4.5 natively on the Mac mini (23.10.2026; the owner: both WRF runs on the Mac), against the WRF that
# mac_build.sh made. Needs: brew install jasper libpng, and the source in ~/wrf/WPS:
#   git clone --depth 1 --branch v4.5 https://github.com/wrf-model/WPS.git
# configure option 21: Darwin gfortran/clang, serial with GRIB2 (geogrid, ungrib and metgrid run unparallelised,
# as on the Tower). gfortran 10+ wants the old Fortran's argument mismatches and BOZ constants allowed, and Apple's
# clang refuses ungrib's old C (cio.c: implicit int) unless it's compiled as gnu89.
export PATH=/opt/homebrew/bin:$PATH
export NETCDF=$HOME/wrf/netcdf WRF_DIR=$HOME/wrf/WRF
export JASPERLIB=$(brew --prefix jasper)/lib JASPERINC=$(brew --prefix jasper)/include
PNG=$(brew --prefix libpng)
cd ~/wrf/WPS
./clean -a > /dev/null 2>&1
printf '21\n' | ./configure > ~/wrf/configure-wps.log 2>&1
echo "configure exit $?"
# JPEG2000 stays compiled in (ungrib reads GRIB2 only with both USE_PNG and USE_JPEG2000), but its decoder calls
# jasper's old jpc_decode, gone from Homebrew's jasper 4: patched to the public jas_image_decode. Our GFS (NOMADS's
# filter) is all simple packing (23.10.2026: 2182 of 2182 fields), so the decoder isn't used; it only has to link.
sed -i '' 's/image=jpc_decode(jpcstream,opts);/image=jas_image_decode(jpcstream,jas_image_strtofmt("jpc"),opts);/' \
  ungrib/src/ngl/g2/dec_jpeg2000.c
grep -q "jas_image_decode" ungrib/src/ngl/g2/dec_jpeg2000.c || { echo "dec_jpeg2000.c not patched"; exit 1; }
# libpng and zlib beside jasper (configure.wps names only jasper's folder).
sed -i '' -e "s#^COMPRESSION_LIBS.*#COMPRESSION_LIBS = -L$JASPERLIB -ljasper -L$PNG/lib -lpng -lz#" \
          -e "s#^COMPRESSION_INC.*#COMPRESSION_INC = -I$JASPERINC -I$PNG/include#" \
          -e "s#^\(FFLAGS *=.*\)#\1 -fallow-argument-mismatch -fallow-invalid-boz#" \
          -e "s#^\(F77FLAGS *=.*\)#\1 -fallow-argument-mismatch -fallow-invalid-boz#" \
          -e "s#^\(CFLAGS *=.*\)#\1 -std=gnu89#" configure.wps
grep -E "^(FC|SFC|SCC|CPP|FFLAGS|COMPRESSION_LIBS|WRF_LIB) " configure.wps
start=$(date +%s)
./compile > ~/wrf/compile-wps.log 2>&1
echo "compile took $(( $(date +%s) - start )) s"
ls -la geogrid.exe ungrib.exe metgrid.exe 2>&1
echo WPS-BUILD-DONE

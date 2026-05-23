import { fromArrayBuffer } from 'geotiff';
import fs from 'fs';

async function debugGeoTIFF() {
  try {
    const data = fs.readFileSync('data/malapert-psr.tif');
    const arrayBuffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
    const tiff = await fromArrayBuffer(arrayBuffer);
    const image = await tiff.getImage();
    
    console.log('SamplesPerPixel:', image.getSamplesPerPixel());
    console.log('PhotometricInterpretation:', image.getFileDirectory().PhotometricInterpretation);
    console.log('BitsPerSample:', image.getFileDirectory().BitsPerSample);
    
  } catch (e) {
    console.error(e);
  }
}

debugGeoTIFF();
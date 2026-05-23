/**
 * Export functionality for PNG and PDF.
 */

import { jsPDF } from 'jspdf';

/**
 * Export map as PNG.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {number} scale - Scale factor for resolution (default: 1.0)
 * @returns {Promise<string>} Base64-encoded PNG data
 */
export async function exportMapToPNG(map, scale = 1.0) {
  return new Promise((resolve, reject) => {
    try {
      const mapCanvas = document.createElement('canvas');
      const size = map.getSize();
      
      if (!size) {
        reject(new Error('Map has no size'));
        return;
      }
      
      mapCanvas.width = size[0] * scale;
      mapCanvas.height = size[1] * scale;
      const mapContext = mapCanvas.getContext('2d');
      
      // Scale context for higher resolution
      mapContext.scale(scale, scale);
      
      // Get all canvas elements from the map
      const canvases = map.getViewport().querySelectorAll('.ol-layer canvas, canvas.ol-layer');
      
      if (canvases.length === 0) {
        reject(new Error('No canvas elements found in map'));
        return;
      }
      
      // Composite all layer canvases
      Array.from(canvases).forEach((canvas) => {
        if (canvas.width > 0) {
          const opacity = canvas.parentNode.style.opacity || 1;
          mapContext.globalAlpha = opacity === '' ? 1 : Number(opacity);
          
          // Get transform
          const transform = canvas.style.transform;
          const matrix = transform
            .match(/^matrix\(([^\(]*)\)$/)?.[1]
            ?.split(',')
            ?.map(Number);
          
          // Apply transform if present
          if (matrix) {
            mapContext.save();
            mapContext.transform(...matrix);
            mapContext.drawImage(canvas, 0, 0);
            mapContext.restore();
          } else {
            mapContext.drawImage(canvas, 0, 0);
          }
        }
      });
      
      // Convert to base64
      const dataURL = mapCanvas.toDataURL('image/png');
      
      // Return base64 data (remove data URL prefix)
      const base64 = dataURL.split(',')[1];
      resolve(base64);
    } catch (error) {
      console.error('Error exporting to PNG:', error);
      reject(error);
    }
  });
}

/**
 * Export map as PDF.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {string} size - Paper size ('A4', 'A3', 'letter', etc.)
 * @param {number} dpi - Resolution in DPI (default: 150)
 * @returns {Promise<string>} Base64-encoded PDF data
 */
export async function exportMapToPDF(map, size = 'A4', dpi = 150) {
  try {
    // Calculate scale for DPI
    const scale = dpi / 96;  // 96 is standard screen DPI
    
    // First export as PNG at high resolution
    const pngBase64 = await exportMapToPNG(map, scale);
    
    // Define paper dimensions (in mm)
    const paperSizes = {
      'A4': { width: 210, height: 297 },
      'A3': { width: 297, height: 420 },
      'letter': { width: 215.9, height: 279.4 },
      'legal': { width: 215.9, height: 355.6 }
    };
    
    const paperSize = paperSizes[size] || paperSizes['A4'];
    
    // Determine orientation based on map aspect ratio
    const mapSize = map.getSize();
    const mapAspect = mapSize[0] / mapSize[1];
    const paperAspect = paperSize.width / paperSize.height;
    
    let orientation = 'portrait';
    let pdfWidth = paperSize.width;
    let pdfHeight = paperSize.height;
    
    if (mapAspect > 1) {
      orientation = 'landscape';
      pdfWidth = paperSize.height;
      pdfHeight = paperSize.width;
    }
    
    // Create PDF
    const pdf = new jsPDF({
      orientation: orientation,
      unit: 'mm',
      format: size.toLowerCase()
    });
    
    // Calculate image dimensions to fit page (with margins)
    const margin = 10;
    const maxWidth = pdfWidth - (2 * margin);
    const maxHeight = pdfHeight - (2 * margin);
    
    let imgWidth = maxWidth;
    let imgHeight = (maxWidth / mapSize[0]) * mapSize[1];
    
    if (imgHeight > maxHeight) {
      imgHeight = maxHeight;
      imgWidth = (maxHeight / mapSize[1]) * mapSize[0];
    }
    
    // Center image on page
    const x = (pdfWidth - imgWidth) / 2;
    const y = (pdfHeight - imgHeight) / 2;
    
    // Add image to PDF
    pdf.addImage(
      `data:image/png;base64,${pngBase64}`,
      'PNG',
      x,
      y,
      imgWidth,
      imgHeight
    );
    
    // Add title/metadata
    pdf.setProperties({
      title: 'Moon Map Export',
      subject: 'Lunar South Polar Map',
      author: 'MoonLayers',
      creator: 'MoonLayers'
    });
    
    // Convert PDF to base64
    const pdfBase64 = pdf.output('dataurlstring').split(',')[1];
    
    return pdfBase64;
  } catch (error) {
    console.error('Error exporting to PDF:', error);
    throw error;
  }
}

/**
 * Download base64 data as file.
 * 
 * @param {string} base64Data - Base64-encoded data
 * @param {string} filename - Filename for download
 * @param {string} mimeType - MIME type (e.g., 'image/png')
 */
export function downloadFile(base64Data, filename, mimeType) {
  try {
    const dataURL = `data:${mimeType};base64,${base64Data}`;
    const link = document.createElement('a');
    link.href = dataURL;
    link.download = filename;
    link.style.display = 'none';
    
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    console.log(`Downloaded ${filename}`);
  } catch (error) {
    console.error('Error downloading file:', error);
    throw error;
  }
}

/**
 * Trigger map render for export (ensures all tiles are loaded).
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @returns {Promise<void>}
 */
export function ensureMapRendered(map) {
  return new Promise((resolve) => {
    map.once('rendercomplete', () => {
      // Wait a bit more to ensure everything is ready
      setTimeout(resolve, 100);
    });
    map.render();
  });
}

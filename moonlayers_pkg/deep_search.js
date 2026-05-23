/**
 * DEEP SEARCH - Find the layer switcher anywhere in the page
 * Including iframes and shadow DOM
 */

(function() {
  console.log('=== DEEP SEARCH FOR LAYER SWITCHER ===');
  
  // Function to search in a document (could be main or iframe)
  function searchInDocument(doc, label) {
    console.log(`\nSearching in: ${label}`);
    
    const layerSwitcher = doc.querySelector('.layer-switcher');
    if (layerSwitcher) {
      console.log(`✓ FOUND in ${label}!`, layerSwitcher);
      return layerSwitcher;
    }
    
    // Also try other selectors
    const mapContainer = doc.querySelector('.moonlayers-map');
    if (mapContainer) {
      console.log(`✓ Found map container in ${label}`, mapContainer);
    }
    
    const olMap = doc.querySelector('.ol-viewport');
    if (olMap) {
      console.log(`✓ Found OpenLayers map in ${label}`, olMap);
    }
    
    return null;
  }
  
  // 1. Search main document
  let found = searchInDocument(document, 'main document');
  
  // 2. Search all iframes
  const iframes = document.querySelectorAll('iframe');
  console.log(`\nFound ${iframes.length} iframes`);
  
  iframes.forEach((iframe, index) => {
    try {
      console.log(`\nChecking iframe ${index}:`, iframe);
      console.log('  src:', iframe.src);
      console.log('  id:', iframe.id);
      console.log('  class:', iframe.className);
      
      const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
      const result = searchInDocument(iframeDoc, `iframe ${index}`);
      if (result && !found) {
        found = result;
        console.log(`✓✓✓ Layer switcher is in iframe ${index}! ✓✓✓`);
        console.log('To access it, use:');
        console.log(`  const iframe = document.querySelectorAll('iframe')[${index}];`);
        console.log(`  const doc = iframe.contentDocument || iframe.contentWindow.document;`);
        console.log(`  const layerSwitcher = doc.querySelector('.layer-switcher');`);
      }
    } catch (e) {
      console.warn(`Cannot access iframe ${index}:`, e.message);
    }
  });
  
  // 3. Search shadow DOM
  const elementsWithShadow = document.querySelectorAll('*');
  let shadowCount = 0;
  elementsWithShadow.forEach(el => {
    if (el.shadowRoot) {
      shadowCount++;
      console.log(`\nFound shadow DOM in:`, el);
      const layerSwitcher = el.shadowRoot.querySelector('.layer-switcher');
      if (layerSwitcher && !found) {
        found = layerSwitcher;
        console.log(`✓✓✓ Layer switcher is in shadow DOM! ✓✓✓`, el);
      }
    }
  });
  console.log(`\nTotal elements with shadow DOM: ${shadowCount}`);
  
  // 4. List all unique class names on page
  console.log('\n=== All unique classes on page (sample) ===');
  const allElements = document.querySelectorAll('*');
  const classNames = new Set();
  allElements.forEach(el => {
    if (el.className && typeof el.className === 'string') {
      el.className.split(' ').forEach(cls => {
        if (cls) classNames.add(cls);
      });
    }
  });
  const classArray = Array.from(classNames);
  console.log(`Total unique classes: ${classArray.length}`);
  console.log('Classes containing "layer":', classArray.filter(c => c.toLowerCase().includes('layer')));
  console.log('Classes containing "map":', classArray.filter(c => c.toLowerCase().includes('map')));
  console.log('Classes containing "widget":', classArray.filter(c => c.toLowerCase().includes('widget')));
  console.log('Classes containing "moon":', classArray.filter(c => c.toLowerCase().includes('moon')));
  
  console.log('\n=== SEARCH COMPLETE ===');
  if (!found) {
    console.error('❌ Layer switcher not found anywhere!');
    console.log('The widget may not be loaded yet, or Marimo uses a different isolation method.');
  }
})();

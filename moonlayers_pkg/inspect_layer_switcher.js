/**
 * INSPECTION SCRIPT - See what's actually in the layer switcher DOM
 * 
 * Open the layer switcher FIRST, then paste this into the console.
 */

(function() {
  console.log('=== INSPECTING LAYER SWITCHER DOM ===');
  
  const layerSwitcher = document.querySelector('.layer-switcher');
  if (!layerSwitcher) {
    console.error('❌ Layer switcher not found!');
    return;
  }
  
  const panel = layerSwitcher.querySelector('.panel');
  if (!panel) {
    console.error('❌ Panel not found! Make sure the layer switcher is OPEN.');
    return;
  }
  
  const layerItems = panel.querySelectorAll('li.layer');
  console.log(`✓ Found ${layerItems.length} layer items`);
  
  layerItems.forEach((li, index) => {
    console.log(`\n--- Layer ${index} ---`);
    console.log('Full HTML:', li.outerHTML);
    console.log('Has .layer-order-controls?', !!li.querySelector('.layer-order-controls'));
    
    const controls = li.querySelector('.layer-order-controls');
    if (controls) {
      console.log('Controls element:', controls);
      console.log('Controls computed style:', {
        display: window.getComputedStyle(controls).display,
        visibility: window.getComputedStyle(controls).visibility,
        opacity: window.getComputedStyle(controls).opacity,
        position: window.getComputedStyle(controls).position,
        width: window.getComputedStyle(controls).width,
        height: window.getComputedStyle(controls).height,
        overflow: window.getComputedStyle(controls).overflow,
        zIndex: window.getComputedStyle(controls).zIndex
      });
      
      const upBtn = controls.querySelector('.layer-order-up');
      const downBtn = controls.querySelector('.layer-order-down');
      
      if (upBtn) {
        console.log('UP button:', upBtn);
        console.log('UP button style:', {
          display: window.getComputedStyle(upBtn).display,
          visibility: window.getComputedStyle(upBtn).visibility,
          width: window.getComputedStyle(upBtn).width,
          height: window.getComputedStyle(upBtn).height
        });
      }
      
      if (downBtn) {
        console.log('DOWN button:', downBtn);
        console.log('DOWN button style:', {
          display: window.getComputedStyle(downBtn).display,
          visibility: window.getComputedStyle(downBtn).visibility,
          width: window.getComputedStyle(downBtn).width,
          height: window.getComputedStyle(downBtn).height
        });
      }
    }
    
    console.log('LI computed style:', {
      display: window.getComputedStyle(li).display,
      position: window.getComputedStyle(li).position,
      overflow: window.getComputedStyle(li).overflow,
      height: window.getComputedStyle(li).height,
      paddingRight: window.getComputedStyle(li).paddingRight
    });
  });
  
  console.log('\n=== Checking for injected styles ===');
  const styleEl = document.getElementById('moonlayers-layer-ordering-styles');
  if (styleEl) {
    console.log('✓ Styles injected');
    console.log('Style content:', styleEl.textContent);
  } else {
    console.log('❌ Styles NOT injected!');
  }
  
  console.log('\n=== DONE ===');
  console.log('Now look at the Elements tab and find .layer-switcher .panel li.layer');
  console.log('Check if .layer-order-controls is visible in the DOM');
})();

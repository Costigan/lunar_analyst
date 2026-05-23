/**
 * EMERGENCY FIX - Manually force layer ordering controls
 * 
 * Paste this entire script into the browser console when your map is loaded.
 * This will forcefully add the ordering controls regardless of timing issues.
 */

(function() {
  console.log('=== FORCING LAYER ORDERING CONTROLS ===');
  
  // Find the layer switcher
  const layerSwitcher = document.querySelector('.layer-switcher');
  if (!layerSwitcher) {
    console.error('❌ Layer switcher not found!');
    return;
  }
  console.log('✓ Found layer switcher:', layerSwitcher);
  
  // Find the panel
  const panel = layerSwitcher.querySelector('.panel');
  if (!panel) {
    console.error('❌ Panel not found! Layer switcher may not be open.');
    console.log('Try clicking the layer switcher button first, then run this script again.');
    return;
  }
  console.log('✓ Found panel:', panel);
  console.log('Panel display:', window.getComputedStyle(panel).display);
  
  // Find all layers
  const layerItems = panel.querySelectorAll('li.layer');
  console.log(`✓ Found ${layerItems.length} layer items`);
  
  if (layerItems.length === 0) {
    console.error('❌ No layers found in the layer switcher!');
    return;
  }
  
  // Remove any existing controls first
  layerSwitcher.querySelectorAll('.layer-order-controls').forEach(el => el.remove());
  console.log('✓ Removed existing controls');
  
  // Add controls to each layer
  let controlsAdded = 0;
  layerItems.forEach((li, index) => {
    const label = li.querySelector('label');
    if (!label) {
      console.warn(`⚠ Layer ${index} has no label, skipping`);
      return;
    }
    
    const layerTitle = label.textContent.trim();
    console.log(`Processing layer ${index}: "${layerTitle}"`);
    
    // Create controls container with VERY OBVIOUS STYLING for debugging
    const controlsDiv = document.createElement('div');
    controlsDiv.className = 'layer-order-controls';
    controlsDiv.style.cssText = `
      display: inline-flex !important;
      gap: 4px !important;
      margin-left: 8px !important;
      padding: 2px !important;
      background: rgba(255, 255, 0, 0.3) !important;
      border: 2px dashed red !important;
      vertical-align: middle !important;
    `;
    
    // Create up button with VERY VISIBLE styling
    const upBtn = document.createElement('button');
    upBtn.className = 'layer-order-btn layer-order-up';
    upBtn.innerHTML = '↑';
    upBtn.title = 'Move layer up';
    upBtn.style.cssText = `
      background: #4CAF50 !important;
      color: white !important;
      border: 2px solid #2E7D32 !important;
      border-radius: 4px !important;
      width: 28px !important;
      height: 28px !important;
      font-size: 16px !important;
      font-weight: bold !important;
      line-height: 1 !important;
      cursor: pointer !important;
      padding: 0 !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
    `;
    upBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      alert(`UP button clicked for: ${layerTitle}\n\n(Button is working, but actual reordering logic may not be connected)`);
    };
    
    // Create down button with VERY VISIBLE styling
    const downBtn = document.createElement('button');
    downBtn.className = 'layer-order-btn layer-order-down';
    downBtn.innerHTML = '↓';
    downBtn.title = 'Move layer down';
    downBtn.style.cssText = `
      background: #2196F3 !important;
      color: white !important;
      border: 2px solid #1565C0 !important;
      border-radius: 4px !important;
      width: 28px !important;
      height: 28px !important;
      font-size: 16px !important;
      font-weight: bold !important;
      line-height: 1 !important;
      cursor: pointer !important;
      padding: 0 !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
    `;
    downBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      alert(`DOWN button clicked for: ${layerTitle}\n\n(Button is working, but actual reordering logic may not be connected)`);
    };
    
    controlsDiv.appendChild(upBtn);
    controlsDiv.appendChild(downBtn);
    
    // Append to the layer item
    li.appendChild(controlsDiv);
    controlsAdded++;
    
    console.log(`✓ Added controls to: "${layerTitle}"`);
  });
  
  console.log(`=== DONE: Added controls to ${controlsAdded}/${layerItems.length} layers ===`);
  console.log('The buttons should now be visible with:');
  console.log('- Green UP button (↑)');
  console.log('- Blue DOWN button (↓)');
  console.log('- Yellow background and red dashed border around controls');
  console.log('');
  console.log('If you STILL cannot see them:');
  console.log('1. The layer switcher panel might be closing');
  console.log('2. There might be CSS z-index or overflow issues');
  console.log('3. The buttons might be positioned outside the visible area');
  console.log('');
  console.log('Try inspecting the layer switcher panel in DevTools Elements tab.');
})();

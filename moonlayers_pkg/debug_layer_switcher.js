/**
 * Debug script to inspect layer switcher DOM structure.
 * Paste this into the browser console when the map is loaded.
 */

// Find the layer switcher element
const layerSwitcher = document.querySelector('.layer-switcher');
console.log('Layer switcher element:', layerSwitcher);

if (layerSwitcher) {
  // Find the panel
  const panel = layerSwitcher.querySelector('.panel');
  console.log('Panel element:', panel);
  console.log('Panel display:', panel ? panel.style.display : 'N/A');
  console.log('Panel computed display:', panel ? window.getComputedStyle(panel).display : 'N/A');
  
  // Find all layer list items
  const layerItems = layerSwitcher.querySelectorAll('li.layer');
  console.log(`Found ${layerItems.length} layer items`);
  
  layerItems.forEach((li, index) => {
    const label = li.querySelector('label');
    const title = label ? label.textContent.trim() : 'Unknown';
    const hasControls = !!li.querySelector('.layer-order-controls');
    
    console.log(`Layer ${index}:`, {
      title: title,
      element: li,
      hasOrderingControls: hasControls,
      innerHTML: li.innerHTML
    });
  });
  
  // Check if styles are injected
  const stylesEl = document.getElementById('moonlayers-layer-ordering-styles');
  console.log('Layer ordering styles injected:', !!stylesEl);
  if (stylesEl) {
    console.log('Styles content:', stylesEl.textContent);
  }
  
  // Try to manually add controls to demonstrate what they should look like
  layerItems.forEach((li, index) => {
    if (li.querySelector('.layer-order-controls')) {
      console.log(`Layer ${index} already has controls`);
      return;
    }
    
    const label = li.querySelector('label');
    if (!label) return;
    
    const controlsDiv = document.createElement('div');
    controlsDiv.className = 'layer-order-controls';
    controlsDiv.style.cssText = 'display: inline-flex; gap: 2px; margin-left: 8px; background: yellow;'; // Yellow for debugging
    
    const upBtn = document.createElement('button');
    upBtn.className = 'layer-order-btn layer-order-up';
    upBtn.innerHTML = '↑';
    upBtn.style.cssText = 'width: 22px; height: 22px; background: lightblue; border: 1px solid black; cursor: pointer;';
    
    const downBtn = document.createElement('button');
    downBtn.className = 'layer-order-btn layer-order-down';
    downBtn.innerHTML = '↓';
    downBtn.style.cssText = 'width: 22px; height: 22px; background: lightblue; border: 1px solid black; cursor: pointer;';
    
    controlsDiv.appendChild(upBtn);
    controlsDiv.appendChild(downBtn);
    
    li.appendChild(controlsDiv);
    console.log(`Manually added controls to layer ${index}`);
  });
} else {
  console.error('Layer switcher not found!');
}

/**
 * Layer catalog search UI for Trek layers.
 */

import { Control } from 'ol/control';

/**
 * Create a collapsible layer search panel.
 * 
 * @param {Object} model - Anywidget model for syncing search
 * @param {Function} onAddLayer - Callback when layer is added from UI
 * @param {Function} onRemoveLayer - Callback when layer is removed from UI
 * @returns {HTMLElement} Panel element
 */
export function createLayerSearchPanel(model, onAddLayer, onRemoveLayer) {
  // Create panel container
  const panel = document.createElement('div');
  panel.className = 'trek-layer-search-panel';
  panel.style.cssText = `
    position: absolute;
    top: 10px;
    left: 10px;
    width: 320px;
    background: white;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    z-index: 1000;
    font-family: sans-serif;
    font-size: 13px;
  `;
  
  // Create header with toggle
  const header = document.createElement('div');
  header.style.cssText = `
    padding: 10px;
    background: #f0f0f0;
    border-bottom: 1px solid #ccc;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-radius: 4px 4px 0 0;
  `;
  header.innerHTML = `
    <strong>Search Trek Layers</strong>
    <span class="toggle-icon">▼</span>
  `;
  
  // Create content area
  const content = document.createElement('div');
  content.className = 'search-content';
  content.style.cssText = `
    padding: 10px;
    max-height: 400px;
    overflow-y: auto;
  `;
  
  // Search input
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.placeholder = 'Search layers...';
  searchInput.style.cssText = `
    width: 100%;
    padding: 6px 8px;
    border: 1px solid #ccc;
    border-radius: 3px;
    box-sizing: border-box;
    margin-bottom: 8px;
  `;
  
  // Help text
  const helpText = document.createElement('div');
  helpText.style.cssText = `
    font-size: 11px;
    color: #666;
    margin-bottom: 10px;
    padding: 6px;
    background: #f9f9f9;
    border-radius: 3px;
  `;
  helpText.innerHTML = `
    Use <code>AND</code>, <code>OR</code>, <code>NOT</code> (or <code>&</code>, <code>|</code>, <code>-</code>)<br>
    Example: <code>(Artemis OR Apollo) AND -crater</code>
  `;
  
  // Results container
  const resultsContainer = document.createElement('div');
  resultsContainer.className = 'search-results';
  resultsContainer.style.cssText = `
    border-top: 1px solid #eee;
    padding-top: 8px;
  `;
  
  // Initial message
  const initialMessage = document.createElement('div');
  initialMessage.style.cssText = `
    color: #999;
    text-align: center;
    padding: 20px;
  `;
  initialMessage.textContent = 'Enter search terms above';
  resultsContainer.appendChild(initialMessage);
  
  content.appendChild(searchInput);
  content.appendChild(helpText);
  content.appendChild(resultsContainer);
  
  panel.appendChild(header);
  panel.appendChild(content);
  
  // Toggle collapse
  let isCollapsed = false;
  let hasBeenOpened = false;  // Track if panel has been opened before
  
  header.addEventListener('click', () => {
    isCollapsed = !isCollapsed;
    content.style.display = isCollapsed ? 'none' : 'block';
    header.querySelector('.toggle-icon').textContent = isCollapsed ? '▶' : '▼';
    
    // Emit event when panel is opened for the first time
    if (!isCollapsed && !hasBeenOpened) {
      hasBeenOpened = true;
      
      // Send event to Python to trigger auto-fetch
      model.set('_event', {
        type: 'search_panel_opened',
        timestamp: Date.now()
      });
      model.save_changes();
      
      console.log('Layer search panel opened - requesting catalog fetch');
    }
  });
  
  // Search functionality
  let searchTimeout = null;
  
  searchInput.addEventListener('input', (e) => {
    const query = e.target.value.trim();
    
    // Debounce search
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      performSearch(query);
    }, 300);
  });
  
  /**
   * Perform search on Trek layers
   */
  function performSearch(query) {
    let layers = model.get('trek_layers') || [];
    
    // Filter out tour layers
    layers = layers.filter(layer => layer.productLabel !== 'tour');
    
    // Show loading if no layers yet
    if (layers.length === 0) {
      resultsContainer.innerHTML = `
        <div style="color: #666; text-align: center; padding: 20px;">
          <div style="margin-bottom: 8px;">⏳ Loading layer catalog...</div>
          <div style="font-size: 11px;">This may take a few seconds</div>
        </div>
      `;
      return;
    }
    
    if (!query) {
      resultsContainer.innerHTML = `
        <div style="color: #999; text-align: center; padding: 20px;">
          Enter search terms above
        </div>
      `;
      return;
    }
    
    // Simple client-side search (Python does the heavy lifting)
    // For now, do basic filtering
    const lowerQuery = query.toLowerCase();
    const matches = layers.filter(layer => {
      const searchText = [
        layer.productLabel || '',
        layer.title || '',
        layer.description || ''
      ].join(' ').toLowerCase();
      
      // Basic check - just see if query terms are in text
      // The Python search_layers() does the proper boolean logic
      const terms = lowerQuery.split(/\s+/).filter(t => 
        !['and', 'or', 'not', '&', '|', '-', '(', ')'].includes(t)
      );
      
      return terms.some(term => searchText.includes(term));
    });
    
    displayResults(matches);
  }
  
  /**
   * Display search results
   */
  function displayResults(layers) {
    resultsContainer.innerHTML = '';
    
    if (layers.length === 0) {
      resultsContainer.innerHTML = `
        <div style="color: #999; text-align: center; padding: 20px;">
          No layers found
        </div>
      `;
      return;
    }
    
    // Show count
    const countDiv = document.createElement('div');
    countDiv.style.cssText = `
      font-size: 11px;
      color: #666;
      margin-bottom: 8px;
    `;
    countDiv.textContent = `Found ${layers.length} layer${layers.length !== 1 ? 's' : ''}`;
    resultsContainer.appendChild(countDiv);
    
    // Create list
    const list = document.createElement('ul');
    list.style.cssText = `
      list-style: none;
      padding: 0;
      margin: 0;
    `;
    
    layers.forEach(layer => {
      const item = document.createElement('li');
      item.style.cssText = `
        padding: 8px;
        margin-bottom: 4px;
        background: #f9f9f9;
        border: 1px solid #e0e0e0;
        border-radius: 3px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        transition: background 0.2s;
      `;
      
      item.addEventListener('mouseenter', () => {
        item.style.background = '#e8f4f8';
      });
      
      item.addEventListener('mouseleave', () => {
        item.style.background = '#f9f9f9';
      });
      
      const titleSpan = document.createElement('span');
      titleSpan.textContent = layer.title || layer.productLabel;
      titleSpan.style.cssText = `
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      `;
      titleSpan.title = `${layer.title || layer.productLabel}\nProduct: ${layer.productLabel}`;
      
      // Check if layer is already added
      const activeLayers = model.get('active_layers') || [];
      const isAdded = activeLayers.includes(layer.item_UUID);
      
      const actionButton = document.createElement('button');
      actionButton.textContent = isAdded ? '−' : '+';
      actionButton.style.cssText = `
        background: ${isAdded ? '#f44336' : '#4CAF50'};
        color: white;
        border: none;
        border-radius: 3px;
        width: 24px;
        height: 24px;
        cursor: pointer;
        font-size: 16px;
        line-height: 1;
        flex-shrink: 0;
        margin-left: 8px;
      `;
      actionButton.title = isAdded ? 'Remove layer from map' : 'Add layer to map';
      
      actionButton.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isAdded) {
          // Remove layer
          onRemoveLayer(layer);
          actionButton.textContent = '+';
          actionButton.style.background = '#4CAF50';
          actionButton.title = 'Add layer to map';
        } else {
          // Add layer
          onAddLayer(layer);
          actionButton.textContent = '−';
          actionButton.style.background = '#f44336';
          actionButton.title = 'Remove layer from map';
        }
      });
      
      item.appendChild(titleSpan);
      item.appendChild(actionButton);
      
      // Context menu for Trek website link
      item.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const trekUrl = `https://trek.nasa.gov/moon/#v=0.1&x=${layer.item_UUID}`;
        window.open(trekUrl, '_blank');
      });
      
      list.appendChild(item);
    });
    
    resultsContainer.appendChild(list);
  }
  
  // Initial load of layers from model
  const initialLayers = model.get('trek_layers');
  if (initialLayers && initialLayers.length > 0) {
    // Don't show all layers by default
    console.log(`Trek catalog loaded: ${initialLayers.length} layers available`);
  }
  
  // Listen for trek_layers updates from Python
  model.on('change:trek_layers', () => {
    const layers = model.get('trek_layers') || [];
    console.log(`Trek layers updated from Python: ${layers.length} layers`);
    
    // Update the UI to show layers are loaded (if not already showing search results)
    if (layers.length > 0) {
      const hasQuery = searchInput.value.trim();
      
      if (hasQuery) {
        // Re-run search with the query
        performSearch(hasQuery);
      } else {
        // Show success message if no search query
        resultsContainer.innerHTML = `
          <div style="color: #4CAF50; text-align: center; padding: 20px;">
            <div style="font-size: 14px; margin-bottom: 4px;">✓ ${layers.length} layers loaded</div>
            <div style="font-size: 11px; color: #666;">Enter search terms above</div>
          </div>
        `;
      }
    }
  });
  
  // Listen for active_layers updates to refresh button states
  model.on('change:active_layers', () => {
    console.log('Active layers updated');
    // Re-run search if there's a query to update button states
    if (searchInput.value.trim()) {
      performSearch(searchInput.value.trim());
    }
  });
  
  return panel;
}

/**
 * Create an OpenLayers control for the search panel.
 * 
 * @param {Object} model - Anywidget model
 * @param {Function} onAddLayer - Callback when layer is added
 * @param {Function} onRemoveLayer - Callback when layer is removed
 * @returns {Control} OpenLayers control
 */
export function createSearchControl(model, onAddLayer, onRemoveLayer) {
  const panel = createLayerSearchPanel(model, onAddLayer, onRemoveLayer);
  
  const SearchControl = class extends Control {
    constructor(opt_options) {
      const options = opt_options || {};
      super({
        element: panel,
        target: options.target
      });
    }
  };
  
  return new SearchControl();
}

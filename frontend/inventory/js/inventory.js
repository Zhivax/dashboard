class InventoryManager {
    constructor() {
        this.baseURL = 'http://localhost:8000/inventory';
        this.authBaseURL = 'http://localhost:8000/auth';
        this.authToken = localStorage.getItem('token');
        this.currentEditId = null;
        this.items = [];
        
        this.init();
    }

    init() {
        this.checkAuth();
        this.bindEvents();
        this.loadInventory();
    }

    checkAuth() {
        if (!this.authToken) {
            window.location.href = '../auth/login.html';
            return;
        }
    }

    bindEvents() {
        // Navigation
        document.getElementById('logoutBtn').addEventListener('click', () => this.logout());
        
        // Modal controls
        document.getElementById('addItemBtn').addEventListener('click', () => this.openAddModal());
        document.getElementById('closeModal').addEventListener('click', () => this.closeModal());
        document.getElementById('cancelBtn').addEventListener('click', () => this.closeModal());
        
        // Low stock
        document.getElementById('lowStockBtn').addEventListener('click', () => this.openLowStockModal());
        document.getElementById('closeLowStockModal').addEventListener('click', () => this.closeLowStockModal());
        document.getElementById('checkLowStockBtn').addEventListener('click', () => this.checkLowStock());
        
        // Form submission
        document.getElementById('itemForm').addEventListener('submit', (e) => this.handleFormSubmit(e));
        
        // Search and filter
        document.getElementById('searchInput').addEventListener('input', (e) => this.filterItems());
        document.getElementById('typeFilter').addEventListener('change', (e) => this.filterItems());
        
        // Close modal when clicking outside
        document.getElementById('itemModal').addEventListener('click', (e) => {
            if (e.target.id === 'itemModal') this.closeModal();
        });
        
        document.getElementById('lowStockModal').addEventListener('click', (e) => {
            if (e.target.id === 'lowStockModal') this.closeLowStockModal();
        });
    }

    async loadInventory() {
        try {
            this.showLoading(true);
            const response = await fetch(`${this.baseURL}/items`, {
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (response.status === 401) {
                this.showMessage('Session expired. Please login again.', 'error');
                setTimeout(() => window.location.href = '../auth/login.html', 2000);
                return;
            }

            if (!response.ok) {
                throw new Error('Failed to load inventory');
            }

            const data = await response.json();
            this.items = data.items || [];
            this.renderInventory();
            this.updateStats();
        } catch (error) {
            console.error('Error loading inventory:', error);
            this.showMessage('Failed to load inventory items', 'error');
        } finally {
            this.showLoading(false);
        }
    }

    renderInventory(itemsToRender = null) {
        const items = itemsToRender || this.items;
        const tbody = document.getElementById('inventoryTableBody');
        const noDataMsg = document.getElementById('noDataMessage');
        
        if (items.length === 0) {
            tbody.innerHTML = '';
            noDataMsg.style.display = 'block';
            return;
        }
        
        noDataMsg.style.display = 'none';
        
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${this.escapeHtml(item.name)}</td>
                <td>
                    <span class="type-badge type-${item.type}">
                        ${item.type.replace('_', ' ')}
                    </span>
                </td>
                <td>
                    <span class="${item.quantity <= 10 ? 'quantity-low' : 'quantity-normal'}">
                        ${item.quantity}
                    </span>
                </td>
                <td>${this.escapeHtml(item.unit)}</td>
                <td>$${parseFloat(item.price || 0).toFixed(2)}</td>
                <td>$${(parseFloat(item.price || 0) * parseInt(item.quantity || 0)).toFixed(2)}</td>
                <td>
                    <div class="action-buttons">
                        <button class="btn-small btn-edit" onclick="inventoryManager.editItem('${item.item_id}')">
                            Edit
                        </button>
                        <button class="btn-small btn-delete" onclick="inventoryManager.deleteItem('${item.item_id}')">
                            Delete
                        </button>
                    </div>
                </td>
            </tr>
        `).join('');
    }

    updateStats() {
        const totalItems = this.items.length;
        const rawMaterials = this.items.filter(item => item.type === 'raw_material').length;
        const finishedProducts = this.items.filter(item => item.type === 'finished_product').length;
        const lowStockItems = this.items.filter(item => item.quantity <= 10).length;
        
        document.getElementById('totalItems').textContent = totalItems;
        document.getElementById('rawMaterials').textContent = rawMaterials;
        document.getElementById('finishedProducts').textContent = finishedProducts;
        document.getElementById('lowStockCount').textContent = lowStockItems;
    }

    filterItems() {
        const searchTerm = document.getElementById('searchInput').value.toLowerCase();
        const typeFilter = document.getElementById('typeFilter').value;
        
        let filteredItems = this.items;
        
        if (searchTerm) {
            filteredItems = filteredItems.filter(item => 
                item.name.toLowerCase().includes(searchTerm) ||
                item.description.toLowerCase().includes(searchTerm)
            );
        }
        
        if (typeFilter) {
            filteredItems = filteredItems.filter(item => item.type === typeFilter);
        }
        
        this.renderInventory(filteredItems);
    }

    openAddModal() {
        this.currentEditId = null;
        document.getElementById('modalTitle').textContent = 'Add New Item';
        document.getElementById('submitBtn').textContent = 'Add Item';
        document.getElementById('itemForm').reset();
        document.getElementById('itemModal').style.display = 'flex';
    }

    async editItem(itemId) {
        try {
            const response = await fetch(`${this.baseURL}/items/${itemId}`, {
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                throw new Error('Failed to load item details');
            }

            const data = await response.json();
            const item = data.item;
            
            this.currentEditId = itemId;
            document.getElementById('modalTitle').textContent = 'Edit Item';
            document.getElementById('submitBtn').textContent = 'Update Item';
            
            // Populate form
            document.getElementById('itemName').value = item.name;
            document.getElementById('itemType').value = item.type;
            document.getElementById('itemQuantity').value = item.quantity;
            document.getElementById('itemUnit').value = item.unit;
            document.getElementById('itemPrice').value = item.price || '';
            document.getElementById('itemDescription').value = item.description || '';
            
            document.getElementById('itemModal').style.display = 'flex';
        } catch (error) {
            console.error('Error loading item:', error);
            this.showMessage('Failed to load item details', 'error');
        }
    }

    async deleteItem(itemId) {
        if (!confirm('Are you sure you want to delete this item?')) {
            return;
        }

        try {
            const response = await fetch(`${this.baseURL}/items/${itemId}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                throw new Error('Failed to delete item');
            }

            this.showMessage('Item deleted successfully', 'success');
            this.loadInventory();
        } catch (error) {
            console.error('Error deleting item:', error);
            this.showMessage('Failed to delete item', 'error');
        }
    }

    async handleFormSubmit(e) {
        e.preventDefault();
        
        const formData = new FormData(e.target);
        const itemData = {
            name: formData.get('name'),
            type: formData.get('type'),
            quantity: parseInt(formData.get('quantity')),
            unit: formData.get('unit'),
            price: parseFloat(formData.get('price')) || 0,
            description: formData.get('description')
        };

        try {
            const url = this.currentEditId 
                ? `${this.baseURL}/items/${this.currentEditId}`
                : `${this.baseURL}/items`;
            
            const method = this.currentEditId ? 'PUT' : 'POST';
            
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(itemData)
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Failed to save item');
            }

            const action = this.currentEditId ? 'updated' : 'created';
            this.showMessage(`Item ${action} successfully`, 'success');
            this.closeModal();
            this.loadInventory();
        } catch (error) {
            console.error('Error saving item:', error);
            this.showMessage(error.message, 'error');
        }
    }

    async checkLowStock() {
        try {
            const threshold = document.getElementById('stockThreshold').value;
            const response = await fetch(`${this.baseURL}/items/low-stock?threshold=${threshold}`, {
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                throw new Error('Failed to check low stock');
            }

            const data = await response.json();
            this.renderLowStockItems(data.items);
        } catch (error) {
            console.error('Error checking low stock:', error);
            this.showMessage('Failed to check low stock items', 'error');
        }
    }

    renderLowStockItems(items) {
        const container = document.getElementById('lowStockList');
        
        if (items.length === 0) {
            container.innerHTML = '<p style="text-align: center; color: #38a169; font-weight: 500;">No low stock items found!</p>';
            return;
        }
        
        container.innerHTML = items.map(item => `
            <div class="low-stock-item">
                <h4>${this.escapeHtml(item.name)}</h4>
                <p><strong>Current Stock:</strong> ${item.quantity} ${item.unit}</p>
                <p><strong>Type:</strong> ${item.type.replace('_', ' ')}</p>
                ${item.description ? `<p><strong>Description:</strong> ${this.escapeHtml(item.description)}</p>` : ''}
            </div>
        `).join('');
    }

    openLowStockModal() {
        document.getElementById('lowStockModal').style.display = 'flex';
        this.checkLowStock();
    }

    closeModal() {
        document.getElementById('itemModal').style.display = 'none';
        this.currentEditId = null;
    }

    closeLowStockModal() {
        document.getElementById('lowStockModal').style.display = 'none';
    }

    showLoading(show) {
        document.getElementById('loadingSpinner').style.display = show ? 'block' : 'none';
    }

    showMessage(message, type = 'info') {
        const container = document.getElementById('messageContainer');
        const messageEl = document.createElement('div');
        messageEl.className = `message ${type}`;
        messageEl.textContent = message;
        
        container.appendChild(messageEl);
        
        setTimeout(() => {
            if (messageEl.parentNode) {
                messageEl.parentNode.removeChild(messageEl);
            }
        }, 5000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async logout() {
        try {
            await fetch(`${this.authBaseURL}/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.authToken}`,
                    'Content-Type': 'application/json'
                }
            });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            localStorage.removeItem('token');
            window.location.href = '../auth/login.html';
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.inventoryManager = new InventoryManager();
});
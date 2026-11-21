(function () {
  var toggle = document.getElementById("nav-toggle");
  var scrim = document.getElementById("scrim");
  function closeNav() { document.body.classList.remove("nav-open"); }
  if (toggle) toggle.addEventListener("click", function () { document.body.classList.toggle("nav-open"); });
  if (scrim) scrim.addEventListener("click", closeNav);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNav();
  });

  var pos = document.getElementById("pos-app");
  if (pos) initPos(pos);
  var po = document.getElementById("po-form");
  if (po) initPo(po);
  var imageEditor = document.querySelector("[data-product-image-editor]");
  if (imageEditor) initProductImageEditor(imageEditor);

  function moneyFactory(currency) {
    var map = {
      USD: { symbol: "$", prefix: true, digits: 2 },
      EUR: { symbol: "€", prefix: true, digits: 2 },
      VND: { symbol: "₫", prefix: false, digits: 0 },
      KHR: { symbol: "KHR", prefix: false, digits: 0 }
    };
    var spec = map[currency] || map.USD;
    return function (value) {
      var text = Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: spec.digits,
        maximumFractionDigits: spec.digits
      });
      return spec.prefix ? spec.symbol + text : text + " " + spec.symbol;
    };
  }

  function initPos(root) {
    var dataNode = document.getElementById("catalog-data");
    var catalog = dataNode ? JSON.parse(dataNode.textContent || "[]") : [];
    var money = moneyFactory(root.dataset.currency || "USD");
    var taxRate = Number(root.dataset.tax || 0);
    var csrf = root.dataset.csrf || "";
    var cart = new Map();
    var activeCat = "";
    var grid = document.getElementById("pos-grid");
    var cats = document.getElementById("pos-cats");
    var lines = document.getElementById("pos-lines");
    var empty = document.getElementById("pos-empty");
    var search = document.getElementById("pos-search");
    var discount = document.getElementById("pos-discount");
    var method = document.getElementById("pos-method");
    var paid = document.getElementById("pos-paid");
    var error = document.getElementById("pos-error");
    var names = [];
    catalog.forEach(function (item) {
      if (item.category && names.indexOf(item.category) === -1) names.push(item.category);
    });
    function chip(label, value) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-tiny" + (value === activeCat ? " btn-primary" : "");
      button.textContent = label;
      button.addEventListener("click", function () {
        activeCat = value;
        drawChips();
        drawGrid();
      });
      return button;
    }
    function drawChips() {
      cats.innerHTML = "";
      cats.appendChild(chip(root.dataset.all || "All", ""));
      names.forEach(function (name) { cats.appendChild(chip(name, name)); });
    }
    function drawGrid() {
      var q = (search.value || "").trim().toLowerCase();
      grid.innerHTML = "";
      catalog.forEach(function (item) {
        if (activeCat && item.category !== activeCat) return;
        if (q && (item.name + " " + item.sku).toLowerCase().indexOf(q) === -1) return;
        var button = document.createElement("button");
        button.type = "button";
        button.className = "sku-card";
        button.disabled = Number(item.stock) <= 0;
        button.innerHTML = "<span class='sku'></span><strong></strong><span class='price'></span><span class='meta'></span>";
        button.querySelector(".sku").textContent = item.sku;
        button.querySelector("strong").textContent = item.name;
        button.querySelector(".price").textContent = money(item.price);
        button.querySelector(".meta").textContent = (Number(item.stock) <= 0 ? "0" : String(item.stock)) + (item.category ? " · " + item.category : "");
        button.addEventListener("click", function () { addItem(item); });
        grid.appendChild(button);
      });
    }
    function addItem(item) {
      var stock = Number(item.stock);
      if (stock <= 0) return;
      var row = cart.get(item.id);
      if (row) {
        if (row.qty + 1 > stock + 1e-9) return;
        row.qty += 1;
      } else {
        cart.set(item.id, { id: item.id, name: item.name, price: Number(item.price), stock: stock, qty: 1 });
      }
      renderCart();
    }
    function renderCart() {
      lines.innerHTML = "";
      cart.forEach(function (item) {
        var row = document.createElement("div");
        row.className = "ticket-line";
        var name = document.createElement("div");
        name.textContent = item.name;
        var qty = document.createElement("input");
        qty.type = "number";
        qty.min = "1";
        qty.step = "1";
        qty.value = String(item.qty);
        qty.addEventListener("input", function () {
          var next = Math.max(1, Math.min(item.stock, Number(qty.value) || 1));
          item.qty = next;
          qty.value = String(next);
          renderTotals();
        });
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "link danger";
        remove.textContent = "×";
        remove.addEventListener("click", function () {
          cart.delete(item.id);
          renderCart();
        });
        row.appendChild(name);
        row.appendChild(qty);
        row.appendChild(remove);
        lines.appendChild(row);
      });
      empty.hidden = cart.size > 0;
      renderTotals();
    }
    function totals() {
      var subtotal = 0;
      cart.forEach(function (item) { subtotal += item.price * item.qty; });
      var off = Math.max(0, Number(discount.value) || 0);
      var base = Math.max(0, subtotal - off);
      var tax = base * taxRate / 100;
      var total = base + tax;
      return { subtotal: subtotal, tax: tax, total: total };
    }
    function renderTotals() {
      var sum = totals();
      document.getElementById("sum-sub").textContent = money(sum.subtotal);
      document.getElementById("sum-rate").textContent = taxRate ? "(" + taxRate + "%)" : "";
      document.getElementById("sum-tax").textContent = money(sum.tax);
      document.getElementById("sum-total").textContent = money(sum.total);
      var cash = method.value === "cash";
      paid.disabled = !cash;
      if (!cash) paid.value = sum.total.toFixed(2);
      var tendered = Number(paid.value) || 0;
      var change = cash ? tendered - sum.total : 0;
      document.getElementById("sum-change").textContent = money(change > 0 ? change : 0);
    }
    search.addEventListener("input", drawGrid);
    discount.addEventListener("input", renderTotals);
    method.addEventListener("change", renderTotals);
    paid.addEventListener("input", renderTotals);
    document.getElementById("pos-pay").addEventListener("click", function () {
      error.hidden = true;
      var payload = {
        lines: Array.from(cart.values()).map(function (item) { return { product_id: item.id, qty: item.qty }; }),
        customer_id: document.getElementById("pos-customer").value || null,
        discount: Number(discount.value) || 0,
        paid: Number(paid.value) || 0,
        payment_method: method.value
      };
      fetch("/pos/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify(payload)
      }).then(function (res) { return res.json(); }).then(function (data) {
        if (!data.ok) {
          error.hidden = false;
          error.textContent = data.error || "Error";
          return;
        }
        window.location.href = data.url;
      }).catch(function () {
        error.hidden = true;
        error.hidden = false;
        error.textContent = "Error";
      });
    });
    drawChips();
    drawGrid();
    renderTotals();
  }

  function initPo(form) {
    var body = document.getElementById("po-lines");
    var tpl = document.getElementById("line-tpl");
    var money = moneyFactory(form.dataset.currency || "USD");
    function recalcAll() {
      var total = 0;
      body.querySelectorAll("tr").forEach(function (row) {
        var qty = Number(row.querySelector(".qty").value) || 0;
        var cost = Number(row.querySelector(".cost").value) || 0;
        var line = qty * cost;
        row.querySelector(".line-total").textContent = money(line);
        total += line;
      });
      var node = document.getElementById("po-total");
      if (node) node.textContent = money(total);
    }
    function bind(row) {
      var select = row.querySelector("select");
      var cost = row.querySelector(".cost");
      select.addEventListener("change", function () {
        var opt = select.selectedOptions[0];
        if (opt && opt.dataset.cost) cost.value = opt.dataset.cost;
        recalcAll();
      });
      row.querySelector(".qty").addEventListener("input", recalcAll);
      cost.addEventListener("input", recalcAll);
      row.querySelector(".remove-line").addEventListener("click", function () {
        if (body.children.length > 1) row.remove();
        recalcAll();
      });
    }
    document.getElementById("add-line").addEventListener("click", function () {
      var row = tpl.content.firstElementChild.cloneNode(true);
      body.appendChild(row);
      bind(row);
    });
    document.getElementById("add-line").click();
  }

  function initProductImageEditor(root) {
    var input = root.querySelector(".product-image-input");
    var preview = root.querySelector("[data-image-preview]");
    var filename = root.querySelector("[data-image-filename]");
    var error = root.querySelector("[data-product-image-error]");
    var dropzone = root.querySelector(".product-image-dropzone");
    var remove = root.querySelector("[data-remove-product-image]");
    var maxBytes = Number(root.dataset.maxBytes) || 0;
    var previewUrl = "";

    function setError(message) {
      error.textContent = message;
      error.hidden = !message;
    }

    function showFile(file) {
      if (!file) return;
      var extension = file.name.toLowerCase().split(".").pop();
      var supported = ["png", "jpg", "jpeg", "webp"].indexOf(extension) !== -1;
      if (!supported) {
        input.value = "";
        setError(root.dataset.invalidMessage || "Choose a PNG, JPEG or WebP image.");
        return;
      }
      if (file.size > maxBytes) {
        input.value = "";
        setError(root.dataset.largeMessage || "Image is too large.");
        return;
      }
      setError("");
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      var image = document.createElement("img");
      image.alt = "";
      image.src = previewUrl;
      preview.replaceChildren(image);
      filename.textContent = file.name + " - " + (file.size < 1048576
        ? Math.max(1, Math.round(file.size / 1024)) + " KB"
        : (file.size / 1048576).toFixed(1) + " MB");
      dropzone.classList.add("has-image");
      if (remove) remove.checked = false;
    }

    input.addEventListener("change", function () {
      showFile(input.files && input.files[0]);
    });
    dropzone.addEventListener("dragover", function (event) {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
    dropzone.addEventListener("dragleave", function (event) {
      if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragging");
    });
    dropzone.addEventListener("drop", function (event) {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
      var file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      input.files = event.dataTransfer.files;
      showFile(file);
    });
    window.addEventListener("pagehide", function () {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    }, { once: true });
  }
})();

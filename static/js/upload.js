(() => {
  "use strict";
  const dropZone = document.getElementById("dropZone");
  const input = dropZone.querySelector('input[type="file"]');
  const fileName = document.getElementById("fileName");

  function showSelection() {
    if (input.files.length) fileName.textContent = input.files[0].name;
  }

  input.addEventListener("change", showSelection);
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-dragging");
    });
  }
  dropZone.addEventListener("drop", (event) => {
    if (!event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    showSelection();
  });
})();

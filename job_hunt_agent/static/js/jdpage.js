const jd = document.getElementById("jd"), b = document.getElementById("copy");
document.getElementById("print").onclick = () => print();
b.onclick = async () => {
  try { await navigator.clipboard.writeText(jd.innerText); } catch { getSelection().selectAllChildren(jd); document.execCommand("copy"); }
  b.textContent = "Copied";
};

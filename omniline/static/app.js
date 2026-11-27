// Progressive enhancement: show the newest messages first.
const scrollMessagesToBottom = () => {
  document.querySelectorAll('.messages').forEach((messages) => {
    messages.scrollTop = messages.scrollHeight;
  });
};

scrollMessagesToBottom();
window.addEventListener('load', scrollMessagesToBottom);

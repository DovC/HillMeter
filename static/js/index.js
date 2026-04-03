const form = document.getElementById('waitlist-form');
const emailInput = document.getElementById('email-input');
const submitBtn = document.getElementById('submit-btn');
const feedback = document.getElementById('feedback');
const socialProof = document.getElementById('social-proof');

// Load waitlist count for social proof
fetch('/api/waitlist/count')
  .then(r => r.json())
  .then(data => {
    if (data.count > 3) {
      socialProof.innerHTML = `<strong>${data.count}</strong> runners already on the list`;
    }
  })
  .catch(() => {});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = emailInput.value.trim();
  if (!email) return;

  submitBtn.disabled = true;
  submitBtn.textContent = 'Joining...';
  feedback.className = 'feedback';
  feedback.textContent = '';

  try {
    const res = await fetch('/api/waitlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, source: 'landing_page' })
    });
    const data = await res.json();

    if (data.status === 'success') {
      feedback.className = 'feedback success';
      feedback.textContent = "You're in! We'll let you know when VertHurt launches.";
      emailInput.value = '';
      emailInput.disabled = true;
      submitBtn.textContent = 'Joined!';
      // Refresh count
      fetch('/api/waitlist/count').then(r => r.json()).then(d => {
        if (d.count > 0) socialProof.innerHTML = `<strong>${d.count}</strong> runners already on the list`;
      });
    } else if (data.status === 'already_registered') {
      feedback.className = 'feedback info';
      feedback.textContent = "You're already on the list! We'll be in touch soon.";
      submitBtn.textContent = 'Already Joined';
    } else {
      throw new Error(data.error || 'Something went wrong');
    }
  } catch (err) {
    feedback.className = 'feedback error';
    feedback.textContent = 'Oops, something went wrong. Try again?';
    submitBtn.disabled = false;
    submitBtn.textContent = 'Join Waitlist';
  }
});

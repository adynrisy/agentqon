# WhatsApp & Telegram

AgentQon doesn't have to live in a terminal. Connect a messaging channel
and a chat message becomes a mission — the verified result comes back as
a reply.

> **Channels need a reachable AgentQon.** Messaging platforms deliver
> events to your server over the internet (webhooks), so channels work
> with a **hosted** AgentQon deployment on a public HTTPS address. A
> purely local install can prepare everything below, but messages only
> flow once your deployment is reachable.

## WhatsApp

AgentQon uses Meta's official **WhatsApp Business Cloud API** — no
unofficial gateways, no QR-code device linking, nothing that risks your
number.

### Connect your number

1. In the web UI, open the WhatsApp panel.
2. Choose **Connect with Facebook** and sign in — Meta's Embedded Signup
   flow walks you through picking (or creating) your WhatsApp Business
   account and number.
3. AgentQon stores your access token encrypted in its vault and
   subscribes itself to your number's events. Disconnecting wipes the
   stored credential completely.

Only accounts you control can be connected, and the signup asks Meta for
exactly the two WhatsApp permissions AgentQon needs
(`whatsapp_business_management`, `whatsapp_business_messaging`) — nothing
else.

### Using it

Message your connected business number with a mission, in plain words.
AgentQon runs it through the same panel → critic → evaluator pipeline as
any other mission and replies with the result.

### Honest limitations

- **The 24-hour window is Meta's rule, not ours.** WhatsApp only allows
  free-form replies within 24 hours of the customer's last message.
  Outside that window, businesses must use pre-approved template
  messages. AgentQon respects this — it will never pretend a message
  was delivered when Meta refused it.
- **Public signup needs Meta's approval.** Until the hosting app passes
  Meta App Review and business verification, Embedded Signup works for
  people with a role on the app. That's a Meta platform rule for every
  WhatsApp product, not an AgentQon limit.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its
   token.
2. In AgentQon's web UI, add the token in the Telegram panel.
3. Message your bot. Whatever you send becomes a mission; the result
   comes back as the reply.

Telegram has no 24-hour window and no app review — it's the fastest
channel to get running once your deployment is reachable.

## Privacy

Channel messages become missions in **your** workspace, processed by
**your** connected model, stored in **your** data directory. AgentQon
adds no tracking and shares nothing with third parties beyond the
platform delivering the message.

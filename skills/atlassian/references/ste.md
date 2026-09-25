# Simplified Technical English for Jira issues

The writing rules for issue summaries and descriptions. They are ASD-STE100 Simplified Technical English, reduced to the parts that apply to a ticket.

ASD owns the copyright of the ASD-STE100 specification and of the full Dictionary. This file holds the writing rules and a small set of word choices. It is not the Dictionary. Get the specification at no cost from <https://asd-ste100.org>.

If you are not sure that a word is approved, use a word from the tables below, or tell the user that the word needs a check against the official Dictionary. Do not claim that a word is approved when you have not checked it.

## Why a ticket is written this way

A ticket is read by a person who did not do the work, often months later, and often in a second language. It is also evidence in a regulated estate. Short sentences and one meaning for each word remove the two failures that cost the most: a reader who acts on the wrong sentence, and a reader who cannot tell a decision from a proposal.

## Sentences

- A sentence has a maximum of 25 words. A sentence that gives an instruction has a maximum of 20 words.
- Write one topic in one sentence.
- Use the active voice. Name the actor: "the team deployed the server", not "the server was deployed".
- Use the simple present tense, the simple past tense, or the simple future tense. Do not write "has been removed". Write "was removed".
- Use the imperative for a request or an instruction. Start with the verb: "Open TCP port 1433 from ...".
- Give the condition before the action. Write "If the quota increases, rebuild the server in North Europe."
- Keep the words that go together near to each other.

## Words

- Use one term for one thing through the whole issue. A `trackingId` does not become "the batch reference" three paragraphs later.
- Do not use a gerund as a noun or as an adjective. Write "before you start the pipeline", not "before starting the pipeline". A technical name is an exception.
- Use a maximum of three nouns together. Break a longer cluster with prepositions.
- Keep the articles "a", "an" and "the". Do not delete them to make a sentence short.
- Write numbers as numerals.
- Do not use a slash. The mark "and/or" is the one exception. A CIDR range, a port notation and a resource ID are technical names and do not change.
- Give the full term at the first use of an abbreviation.
- Do not use an em dash. Write a new sentence.
- Do not use parentheses to add a second idea. Write a new sentence. Parentheses around a reference such as a change number are permitted in a summary.

## Paragraphs

- A paragraph has a maximum of six sentences.
- Write one topic in one paragraph, and start the paragraph with the topic sentence.
- Use a list or a table when you give more than three related items.

## Do not use these words

| Do not use | Use |
|---|---|
| accomplish, execute, perform | do |
| adhere to, comply with | obey |
| ascertain, determine, verify | find, make sure, do a check of |
| assist | help |
| attempt | try |
| cease, terminate | stop, end |
| commence, initiate | start |
| examine, inspect | do a check of, look at |
| facilitate | help, make easy |
| indicate | show |
| leverage, utilize | use |
| modify | change |
| permit | let, open |
| require | need |
| retrospective, retrospectively | after the event, or say what happened: "this ticket records work that is already done" |
| transmit | send |
| adequate, sufficient | enough |
| adjacent | near, next to |
| approximately | about |
| additional | more |
| initial | first |
| numerous, multiple | many |
| previous | before, earlier |
| subsequent | after, next |
| a number of | some, many |
| due to the fact that | because |
| in order to | to |
| in the event that | if |
| is capable of | can |
| it is necessary to | you must, the team needs |
| prior to | before |
| with regard to | about |
| malfunction | fault |
| personnel | persons |
| requirement | need |

Some words carry one approved meaning only. Use "follow" for "to come after" and not for "to obey". Use "about" for "approximately" and not for "on the subject of". Use "only" for "no more than" and not for "alone". Use "test" as a noun: write "do a test", not "test the pump".

## What not to change

- Do not change a technical name, a resource ID, an object ID, an address range, a port number, a date, or a measurement to obey a rule.
- Do not delete a fact to make the text short.
- If a rule and the technical accuracy have a conflict, keep the accuracy and tell the user.

## Check before you create

1. Count the words in each sentence.
2. Find each passive verb. Change it to the active voice where you can.
3. Find each word that ends in "-ing". Change it if it is a noun or an adjective.
4. Find each word in the table above.
5. Make sure that each term is the same through the whole issue.
6. Make sure that the technical data did not change.

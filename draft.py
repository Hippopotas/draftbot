import asyncio
import discord
import json
import random
import requests
import string

from discord.ext import commands

from booster import Booster
from constants import IMG_NOT_FOUND

SCRYFALL_SET_URL = 'https://api.scryfall.com/sets'
EMPTY_POOL_URL = 'https://scryfall.com/search?q=cn%3A-1'


class MTGDraftManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drafts = {}

    def player_in_draft(self, player):
        for draft in self.drafts:
            for drafter in self.drafts[draft].players:
                if player.id == drafter.id:
                    return draft
        
        return None

    # Draft management commands

    @commands.command(brief='Opens signups for a draft.',
                      description=('Opens a normal draft in the format given by mtg_set, '
                                   'with an optional player cap set via max_players.\n'
                                   'The draft fires upon reaching the player cap, or '
                                   'when ;start_draft is called.'))
    async def create_draft(self, ctx, mtg_set, max_players=8):
        def _id_collision(draft_id):
            for draft in self.drafts.values():
                if draft['id'] == draft_id:
                    return True
            return False
        
        def _msg_check(reaction, user):
            return (reaction.message.id == self.signup_id and
                    reaction.emoji == '✋')
        
        mtg_set = mtg_set.upper()
        lower_mtg_set = mtg_set.lower()
        if mtg_set != 'CMR':
            await ctx.send('Unsupported draft format.')
            return
        
        draft_id = ''.join(random.choices(string.ascii_uppercase +
                                          string.digits, k=4))
        while _id_collision(draft_id):
            draft_id = ''.join(random.choices(string.ascii_uppercase +
                                              string.digits, k=4))

        icon_url = IMG_NOT_FOUND
        with requests.get(f'{SCRYFALL_SET_URL}/{lower_mtg_set}') as r:
            if r.status_code == 200:
                icon_url = json.loads(r.text)['icon_svg_uri']

        draft_embed = {'title': f'{ctx.author.display_name}\'s Draft',
                       'description': f'{max_players}-person {mtg_set} draft.\nReact with ✋ to join!',
                       'thumbnail': {'url': f'{icon_url}'},
                       'fields': [{'name': 'Signed Up', 'value': '(Nobody yet!)'},
                                  {'name': 'Draft ID', 'value': (f'Start with ;start_draft {draft_id}.\n'
                                                                 f'Will also fire upon reaching {max_players} people.')},
                                  {'name': 'Status', 'value': 'Open'}
                                 ]}
        print(draft_embed)

        signups = await ctx.send(embed=discord.Embed.from_dict(draft_embed))

        self.drafts[signups.id] = Draft(signups, mtg_set, draft_id, ctx.author.id, max_players)

        curr_draft = self.drafts[signups.id]
        await curr_draft.start.wait()

        curr_draft.in_progress = True
        await self.run_draft(curr_draft)

    async def run_draft(self, curr_draft):
        mtg_set = curr_draft.mtg_set
        players = curr_draft.players

        for player in players:
            await player.send(f'Starting {mtg_set} draft. '
                               'Your first pack will be given shortly.')

        random.shuffle(players)
        draft_table = {}
        for player in players:
            draft_table[player.id] = DraftPlayer(player, mtg_set, curr_draft.id)

        for i, player in enumerate(players):
            left_id = players[(i-1)%len(players)].id
            right_id = players[(i+1)%len(players)].id
            draft_table[player.id].set_neighbors(draft_table[left_id],
                                                 draft_table[right_id])

        for player in players:
            asyncio.create_task(draft_table[player.id].pack_runner(),
                                name=f'{curr_draft.id}_pack_q_{player.id}')
            
            for i in range(1, 4):
                give_pack = Booster(mtg_set, draft_round=i)
                await draft_table[player.id].pack_q.put(give_pack)

        curr_draft.draft_table = draft_table
    
        for _ in range(curr_draft.table_size):
            await curr_draft.done.get()
        
        await self.cleanup_draft(curr_draft)

    async def cleanup_draft(self, curr_draft):
        for task in asyncio.all_tasks():
            if task.get_name().startswith(f'{curr_draft.id}_pack_q_'):
                task.cancel()
        
        del self.drafts[curr_draft.id]

    @commands.command(brief='Starts a given draft pod.',
                      description=('Fires the draft pod with draft_id.\n'
                                   'Pods can only be fired by the person who '
                                   'started them with ;create_draft.'))
    async def start_draft(self, ctx, draft_id):
        for draft in self.drafts.values():
            if ctx.author.id == draft.owner and draft_id == draft.id:
                draft.full = True

                draft_embed = draft.signup_msg.embeds[0].to_dict()
                display_names = []
                for player in draft.players:
                    display_names.append(player.display_name)
                draft_embed['fields'][0]['value'] = ', '.join(display_names)
                draft_embed['fields'][2]['value'] = 'Started'
                await draft.signup_msg.edit(embed=discord.Embed.from_dict(draft_embed))
                draft.start.set()
                break

    @commands.Cog.listener('on_reaction_add')
    async def add_drafter(self, reaction, user):
        if (reaction.message.id in self.drafts and reaction.emoji == '✋'):
            if self.player_in_draft(user):
                await user.send('You cannot join more than one draft at a time.')
                return

            curr_draft = self.drafts[reaction.message.id]
            if not curr_draft.full:
                curr_draft.players.append(user)

                draft_embed = reaction.message.embeds[0].to_dict()
                display_names = []
                for player in curr_draft.players:
                    display_names.append(player.display_name)
                draft_embed['fields'][0]['value'] = ', '.join(display_names)
                await reaction.message.edit(embed=discord.Embed.from_dict(draft_embed))

                if len(curr_draft.players) >= curr_draft.table_size:
                    curr_draft.full = True

                    draft_embed['fields'][2]['value'] = 'Started'
                    await reaction.message.edit(embed=discord.Embed.from_dict(draft_embed))

                    await curr_draft.start.set()

    @commands.Cog.listener('on_raw_reaction_remove')
    async def remove_drafter(self, payload):
        if (payload.message_id in self.drafts and str(payload.emoji) == '✋'):
            curr_draft = self.drafts[payload.message_id]
            player_list = curr_draft.players
            for player in player_list:
                if payload.user_id == player.id:
                    player_list.remove(player)

                    draft_embed = curr_draft.signup_msg.embeds[0].to_dict()
                    display_names = []
                    for player in curr_draft.players:
                        display_names.append(player.display_name)
                    if not display_names:
                        display_names = ['(Nobody yet!)']
                    draft_embed['fields'][0]['value'] = ', '.join(display_names)
                    await curr_draft.signup_msg.edit(embed=discord.Embed.from_dict(draft_embed))

                    break

    # Commands during draft

    @commands.command(brief='Reserves a card during draft.',
                      description=('Reserves the card that matches card_no from a pack '
                                   'to be automatically picked during a draft.\n'
                                   'Currently irrelevant due to draft timers not '
                                   'being implemented yet.'))
    async def reserve(self, ctx, card_no):
        if isinstance(ctx.channel, discord.channelDMChannel):
            draft_id = self.player_in_draft(ctx.author)
            if not draft_id:
                await ctx.send('You are not in a draft right now!')
                return

            player = self.drafts[draft_id].draft_table[ctx.author.id]
            
            if not isinstance(card_no, int):
                print(type(card_no))
                print(card_no)
                return
            if card_no > len(player.curr_pack.cards) or card_no < 1:
                await ctx.send('Invalid pick!')
                return

            card_names = await player.reserve(card_no)
            card_names = '; '.join(card_names)

            await ctx.send(f'Currently reserved: {card_names}')

    @commands.command(brief='Picks a card from a pack during a draft.',
                      description=('Chooses the card that matches card_no from a '
                                   'pack during a draft and adds it to your pool.\n'
                                   'This cannot be undone.'))
    async def pick(self, ctx, card_no):
        if isinstance(ctx.channel, discord.channel.DMChannel):
            draft_id = self.player_in_draft(ctx.author)
            if not draft_id:
                await ctx.send('You are not in a draft right now!')
                return

            try:
                int(card_no)
            except ValueError:
                await ctx.send('Please enter a valid card number.')
                return
            
            player = self.drafts[draft_id].draft_table[ctx.author.id]
            card_no = int(card_no)

            if player.curr_pack:
                if (card_no < 1 or card_no > len(player.curr_pack.cards)):
                    await ctx.send('Please enter a valid, card number.')
                    return
                
                card_name = await player.pick(card_no)

                await ctx.send(f'Picked: {card_name}')
                await player.show_pack()
                player.waiting = False      # End of lock for race condition vs player.pack_runner

    @commands.command(brief='Displays the current pack.',
                      description=('Prints out the contents of the current pack '
                                   'during a draft.'))
    async def pack(self, ctx):
        if isinstance(ctx.channel, discord.channel.DMChannel):
            draft_id = self.player_in_draft(ctx.author)
            if not draft_id:
                await ctx.send('You are not in a draft right now!')
                return

            await self.drafts[draft_id].draft_table[ctx.author.id].show_pack()

    @commands.command(brief='Displays the drafted cardpool.',
                      description=('Shows the current pool of drafted cards '
                                   'during a draft.'))
    async def pool(self, ctx):
        if isinstance(ctx.channel, discord.channel.DMChannel):
            draft_id = self.player_in_draft(ctx.author)
            if not draft_id:
                await ctx.send('You are not in a draft right now!')
                return

            await self.drafts[draft_id].draft_table[ctx.author.id].show_pool()


class Draft():
    def __init__(self, signup_msg, mtg_set, draft_id, owner, table_size):
        self.signup_msg = signup_msg

        self.mtg_set = mtg_set
        self.players = []
        self.id = draft_id
        self.owner = owner
        self.full = False
        self.start = asyncio.Event()

        self.table_size = table_size
        self.in_progress = False
        self.draft_table = {}

        self.done = asyncio.Queue()


class DraftPlayer():
    def __init__(self, player, mtg_set, draft_id):
        self.player = player
        self.draft = draft_id
        self.mtg_set = mtg_set
        self.pack_q = asyncio.Queue()
        self.next_round_q = asyncio.Queue()
        self.done = False

        self.left = None
        self.right = None
        self.curr_round = 1
        self.sub_round = 0

        self.curr_pack = None
        self.pack_msg = None

        self.reserved = []

        self.pool = []
        self.pool_msg = None

        self.waiting = False
        self.num_picks = 0
        self.max_picks = 1
        if self.mtg_set in ('CMR', 'BBD', '2XM'):
            self.max_picks = 2

    async def pack_runner(self):
        while not self.done:
            if not self.curr_pack and not self.waiting:
                new_pack = await self.pack_q.get()
                if new_pack.draft_round == self.curr_round:
                    if len(new_pack.cards) == 0:
                        continue
                    self.curr_pack = new_pack
                    self.sub_round += 1
                    await self.show_pack()
                else:
                    await self.next_round_q.put(new_pack)
                    continue
            await asyncio.sleep(3)

    def set_neighbors(self, left, right):
        self.left = left
        self.right = right

    async def show_pack(self):
        embed_cards = '(Awaiting next pack.)'
        card_images = EMPTY_POOL_URL

        if self.curr_pack:
            embed_cards = []
            for i, card in enumerate(self.curr_pack.cards):
                cardname = card['name']
                if card['is_foil']:
                    cardname += ' \*FOIL\*'
                embed_cards.append(f'{i+1} : {cardname}')
            embed_cards = '\n'.join(embed_cards)

            card_images = Booster.cardlist_to_scryfall(self.curr_pack.cards, self.mtg_set)

        picks_left = self.max_picks - self.num_picks
        embed_desc = f'Picks remaining: {picks_left}\n[Card images]({card_images})'

        pack_embed = {'title': f'Pack {self.curr_round} Pick {self.sub_round}',
                      'description': embed_desc,
                      'fields': [{'name': 'CARDS', 'value': f'{embed_cards}'}
                                ]}
        
        await self.edit_pack_msg(discord.Embed.from_dict(pack_embed))

    async def edit_pack_msg(self, pack_embed):
        if not self.pack_msg:
            self.pack_msg = await self.player.send(embed=pack_embed)
        else:
            await self.pack_msg.delete()
            self.pack_msg = await self.player.send(embed=pack_embed)

    def format_cardpool(self):
        card_counts = {}
        for card in self.pool:
            coll_no = int(card['number'])

            cardname = card['name']
            if card['is_foil']:
                cardname += ' \*FOIL\*'
                coll_no += 0.5

            if coll_no in card_counts:
                card_counts[coll_no]['count'] += 1
            else:
                card_counts[coll_no] = {'count': 1, 'name': cardname}
        
        pool_str = ''
        for _, card_info in card_counts.items():
            count = card_info['count']
            card_name = card_info['name']
            pool_str += f'{count}x {card_name}\n'

        return pool_str

    async def show_pool(self):
        card_images = Booster.cardlist_to_scryfall(self.pool, self.mtg_set)

        embed_cards = self.format_cardpool()
        if not embed_cards:
            embed_cards = '(No cards in pool yet)'
            card_images = EMPTY_POOL_URL

        embed_desc = f'[Card images]({card_images})'
        
        pool_embed = {'title': f'Draft Pool',
                      'description': embed_desc,
                      'fields': [{'name': 'CARDS', 'value': f'{embed_cards}'}
                                ]}

        await self.edit_pool_msg(discord.Embed.from_dict(pool_embed))

    async def edit_pool_msg(self, pool_embed):
        if not self.pool_msg:
            self.pool_msg = await self.player.send(embed=pool_embed)
        else:
            await self.pool_msg.delete()
            self.pool_msg = await self.player.send(embed=pool_embed)

    def reserve(self, card_no):
        max_reserve = 1
        if self.mtg_set in ('CMR', 'BBD', '2XM'):
            max_reserve = 2
        
        if len(self.reserved) >= max_reserve:
            self.reserved.pop(0)
        self.reserved.append(self.curr_pack.cards[card_no-1])

        card_names = []
        for card in self.reserved:
            card_names.append(card['name'])
        
        return card_names

    async def pick(self, card_no):
        self.num_picks += 1
        card_name = self.curr_pack.cards[card_no-1]['name']

        self.pool.append(self.curr_pack.cards.pop(card_no-1))

        if self.num_picks >= self.max_picks:
            await self.pass_pack()

        return card_name
    
    async def pass_pack(self):
        self.reserved = []
        self.num_picks = 0

        to_neighbor = self.left
        if self.curr_round % 2 == 0:
            to_neighbor = self.right

        await to_neighbor.pack_q.put(self.curr_pack)

        if len(self.pool) >= self.curr_round * self.curr_pack.pack_size:
            self.curr_round += 1
            self.sub_round = 0
        
            while not self.pack_q.empty():
                self.next_round_q.put_nowait(self.pack_q.get_nowait())

            self.pack_q = self.next_round_q
            self.next_round_q = asyncio.Queue()
        
        self.waiting = True         # Start of lock for race condition vs player.pack_runner
        self.curr_pack = None
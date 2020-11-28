import asyncio
import discord
import random
import string

from discord.ext import commands

from booster import Booster


class DraftManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drafts = {}

    @commands.command()
    async def create_draft(self, ctx, mtg_set):
        def _id_collision(draft_id):
            for draft in self.drafts.values():
                if draft['id'] == draft_id:
                    return True
            return False
        
        def _msg_check(reaction, user):
            return (reaction.message.id == self.signup_id and
                    reaction.emoji == '✋')
        
        mtg_set = mtg_set.upper()
        if mtg_set != 'CMR':
            await ctx.send('Unsupported draft format.')
            return
        
        draft_id = ''.join(random.choices(string.ascii_lowercase +
                                          string.digits, k=4))
        while _id_collision(draft_id):
            draft_id = ''.join(random.choices(string.ascii_lowercase +
                                              string.digits, k=4))

        signups = await ctx.send(f'{ctx.author.display_name} has started a {mtg_set} draft! '
                                  'React this message with ✋ to join. '
                                 f'Start early with ;start_draft {draft_id}, '
                                  'or it will fire upon reaching 8 drafters.')

        self.drafts[signups.id] = {'set': mtg_set,
                                   'players': [],
                                   'id': draft_id,
                                   'owner': ctx.author.id,
                                   'full': False,
                                   'start': asyncio.Event()}

        curr_draft = self.drafts[signups.id]
        await curr_draft['start'].wait()

        await self.run_draft(curr_draft)

    async def run_draft(self, curr_draft):
        mtg_set = curr_draft['set']
        players = curr_draft['players']

        for player in players:
            await player.send(f'Starting {mtg_set} draft. '
                               'Your first pack will be given shortly.')

        random.shuffle(players)
        draft_table = {}
        for player in players:
            draft_table[player.id] = DraftPlayer(player, mtg_set)

        for i, player in enumerate(players):
            left_id = players[(i-1)%len(players)].id
            right_id = players[(i+1)%len(players)].id
            draft_table[player.id].set_neighbors(draft_table[left_id],
                                                 draft_table[right_id])

        draft_id = curr_draft['id']
        for player in players:
            asyncio.create_task(draft_table[player.id].pack_runner(),
                                name=f'{draft_id}_{player.id}_pack_q')
            
            for i in range(1, 4):
                give_pack = Booster(mtg_set, draft_round=i)
                await draft_table[player.id].pack_q.put(give_pack)

        self.bot.add_cog(Draft(self.bot, mtg_set, draft_table, draft_id,
                               name='draft-'+curr_draft['id']))

    @commands.command()
    async def start_draft(self, ctx, draft_id):
        for draft in self.drafts.values():
            if ctx.author.id == draft['owner'] and draft_id == draft['id']:
                draft['full'] = True
                draft['start'].set()
                break

    @commands.Cog.listener('on_reaction_add')
    async def add_drafter(self, reaction, user):
        if (reaction.message.id in self.drafts and reaction.emoji == '✋'):
            draft = self.drafts[reaction.message.id]
            if not draft['full']:
                draft['players'].append(user)

                if len(draft['players']) >= 8:
                    draft['full'] = True
                    draft['start'].set()

    @commands.Cog.listener('on_raw_reaction_remove')
    async def remove_drafter(self, payload):
        if (payload.message_id in self.drafts and str(payload.emoji) == '✋'):
            player_list = self.drafts[payload.message_id]['players']
            for player in player_list:
                if payload.user_id == player.id:
                    player_list.remove(player)
                    break


class Draft(commands.Cog):
    def __init__(self, bot, mtg_set, players, draft_id):
        self.bot = bot
        self.mtg_set = mtg_set
        self.players = players
        self.id = draft_id

    @commands.command()
    async def reserve(self, ctx, card_no):
        if (isinstance(ctx.channel, discord.channelDMChannel) and
                ctx.author.id in self.players):

            player = self.players[ctx.author.id]
            
            if not isinstance(card_no, int):
                print(type(card_no))
                print(card_no)
                return
            if card_no > len(player.curr_pack.cards) or card_no < 1:
                await ctx.send('Invalid pick!')
                return

            card_names = await player.reserve(card_no)
            card_names = '; '.join(card_names)

            if player.reserve_msg:
                await player.reserve_msg.delete()
            msg = await ctx.send(f'Currently reserved: {card_names}')
            player.reserve_msg = msg

    @commands.command()
    async def pick(self, ctx, card_no):
        if (isinstance(ctx.channel, discord.channel.DMChannel) and
                ctx.author.id in self.players):
            try:
                int(card_no)
            except ValueError:
                await ctx.send('Please enter a valid card number.')
                return
            
            player = self.players[ctx.author.id]
            card_no = int(card_no)

            if player.curr_pack:
                if (card_no < 1 or card_no > len(player.curr_pack) or card_no in player.picked):
                    await ctx.send('Please enter a valid, unpicked card number.')
                    return
                
                card_names = await player.pick(card_no)
                card_names = '; '.join(card_names)

                if player.pick_msg:
                    await player.pick_msg.delete()
                msg = await ctx.send(f'Picked: {card_names}')
                player.pick_msg = msg

    @commands.command()
    async def pack(self, ctx):
        if (isinstance(ctx.channel, discord.channel.DMChannel) and
                ctx.author.id in self.player_ids):
            await self.players[ctx.author.id].show_pack()

    @commands.command()
    async def pool(self, ctx):
        if (isinstance(ctx.channel, discord.channel.DMChannel) and
                ctx.author.id in self.player_ids):
            await self.players[ctx.author.id].show_pool()

class DraftPlayer():
    def __init__(self, player, mtg_set):
        self.player = player
        self.mtg_set = mtg_set
        self.pack_q = asyncio.Queue()
        self.next_round_q = asyncio.Queue()
        self.done = False

        self.left = None
        self.right = None
        self.curr_round = 1

        self.curr_pack = None
        self.pack_msg = None

        self.reserved = []
        self.reserve_msg = None

        self.picked = []
        self.pick_msg = None

        self.pool = []

    async def pack_runner(self):
        while not self.done:
            if not self.curr_pack:
                new_pack = await self.pack_q.get()
                if new_pack.draft_round == self.curr_round:
                    self.curr_pack = new_pack
                    self.show_pack()
                else:
                    self.next_round_q.put(new_pack)
            await asyncio.sleep(3)

    def set_neighbors(self, left, right):
        self.left = left
        self.right = right

    async def show_pack(self):
        if not self.curr_pack:
            await self.player.send('You have no packs.')
        else:
            await self.player.send('Placeholder for how cards should look')
    
    async def show_pool(self):
        await self.player.send('Placeholder for how pool should look')

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
        max_pick = 1
        if self.mtg_set in ('CMR', 'BBD', '2XM'):
            max_pick = 2
        
        self.picked.append(card_no)

        card_names = []
        for card in self.picked:
            card_names.append(self.curr_pack.cards[card-1]['name'])

        if len(self.picked) >= max_pick:
            for cn in self.picked:
                self.pool.append(self.curr_pack.cards[cn-1])
            self.curr_pack.cards = [c for i, c in enumerate(self.curr_pack.cards) if i+1 not in self.picked]

            await self.pass_pack()

        return card_names
    
    async def pass_pack(self):
        self.reserved = []
        self.picked = []
        self.reserve_msg = None
        self.pick_msg = None

        to_neighbor = self.left
        if self.curr_round % 2 == 0:
            to_neighbor = self.right
        
        await to_neighbor.pack_q.put(self.curr_pack)

        if len(self.pool) >= self.curr_round * self.curr_pack.pack_size:
            self.curr_round += 1
        
            while not self.pack_q.empty():
                self.next_round_q.put_nowait(self.pack_q.get_nowait())

            self.pack_q = self.next_round_q
            self.next_round_q = asyncio.Queue()
